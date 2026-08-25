/* TabForge frontend: upload -> poll the job -> render the tabs. */
"use strict";

const $ = (sel) => document.querySelector(sel);

const fileInput = $("#file");
const drop = $("#drop");
const goBtn = $("#go");
const neck = $("#neck");
const emptyBox = $("#empty");
const logEl = $("#log");
const resultsEl = $("#results");

let pickedFile = null;

/* ---------- API access: optional token, readable errors ---------- */

function apiToken() {
  try { return localStorage.getItem("tabforge_token") || ""; } catch { return ""; }
}

function withToken(url) {
  const t = apiToken();
  if (!t) return url;
  return url + (url.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(t);
}

async function apiFetch(url, opts = {}) {
  const t = apiToken();
  opts.headers = Object.assign({}, opts.headers, t ? { "X-API-Token": t } : {});
  let res = await fetch(url, opts);
  if (res.status === 401) {
    const entered = prompt("This server requires an API token:");
    if (entered) {
      try { localStorage.setItem("tabforge_token", entered); } catch {}
      opts.headers = Object.assign({}, opts.headers, { "X-API-Token": entered });
      res = await fetch(url, opts);
    }
  }
  return res;
}

async function errorDetail(res) {
  try { return (await res.json()).detail || `HTTP ${res.status}`; }
  catch { return `HTTP ${res.status}`; }
}

/* ---------- file picking ---------- */

fileInput.addEventListener("change", () => setFile(fileInput.files[0]));

["dragover", "dragenter"].forEach((ev) =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => setFile(e.dataTransfer.files[0]));

function setFile(f) {
  if (!f) return;
  pickedFile = f;
  $("#fileName").textContent = f.name;
  goBtn.disabled = false;
}

/* ---------- starting a job ---------- */

goBtn.addEventListener("click", async () => {
  if (!pickedFile) return;
  goBtn.disabled = true;
  resultsEl.innerHTML = "";
  emptyBox.hidden = true;
  neck.hidden = false;
  neck.classList.add("playing");
  setLog("Uploading the file for processing…");
  markStage(null);

  const stems = [...document.querySelectorAll('input[name="stem"]:checked')]
    .map((c) => c.value).join(",");

  const form = new FormData();
  form.append("file", pickedFile);

  const params = new URLSearchParams({
    stems,
    tuning: $("#tuning").value,
    separate: $("#separate").checked,
    split_guitars: $("#splitGuitars").checked,
  });

  try {
    const res = await apiFetch(`/api/jobs?${params}`, { method: "POST", body: form });
    if (!res.ok) throw new Error(await errorDetail(res));
    const { id } = await res.json();
    poll(id);
  } catch (err) {
    fail(`Failed to start: ${err.message}`);
  }
});

/* ---------- polling ---------- */

const POLL_INTERVAL = 1500;
const MAX_POLL_FAILURES = 4;   // fail only after a series, not one hiccup

async function poll(id, failures = 0) {
  try {
    const res = await apiFetch(`/api/jobs/${id}`);
    if (!res.ok) throw new Error(await errorDetail(res));
    const job = await res.json();

    markStage(job.stage, job.stages);
    if (job.log.length) setLog(job.log[job.log.length - 1]);

    if (job.status === "done") return finish(job);
    if (job.status === "error") return fail(job.error);
    setTimeout(() => poll(id, 0), POLL_INTERVAL);   // success resets the streak
  } catch (err) {
    if (failures + 1 >= MAX_POLL_FAILURES) {
      return fail(`lost contact with the server: ${err.message}`);
    }
    const delay = POLL_INTERVAL * 2 ** (failures + 1);   // 3s, 6s, 12s
    setLog(`connection hiccup, retrying (${failures + 1}/${MAX_POLL_FAILURES - 1})…`);
    setTimeout(() => poll(id, failures + 1), delay);
  }
}

function markStage(current, stages = []) {
  const items = document.querySelectorAll("#frets li");
  let seen = false;
  items.forEach((li) => {
    const s = li.dataset.stage;
    li.classList.remove("active", "done");
    if (s === current) { li.classList.add("active"); seen = true; }
    else if (!seen && current) { li.classList.add("done"); }
  });
  if (current === "done") items.forEach((li) => li.classList.add("done"));
}

function setLog(msg, isError = false) {
  logEl.textContent = msg;
  logEl.classList.toggle("error", isError);
}

function fail(msg) {
  neck.classList.remove("playing");
  setLog(msg, true);
  goBtn.disabled = false;
}

/* ---------- results ---------- */

const STEM_NAMES = { guitar: "Guitar", bass: "Bass", vocals: "Vocals",
                     piano: "Keys", other: "Other", mix: "Full mix",
                     guitar_lead: "Guitar · Lead",
                     guitar_rhythm: "Guitar · Rhythm" };

function finish(job) {
  neck.classList.remove("playing");
  markStage("done");
  setLog("Done. The files are available for download below.");
  goBtn.disabled = false;

  if (!job.results.length) {
    setLog("Processing finished, but no notes were found. Try other stems.", true);
    return;
  }

  const tpl = $("#stemCard");
  for (const r of job.results) {
    const card = tpl.content.cloneNode(true);
    card.querySelector(".stem-name").textContent = STEM_NAMES[r.stem] || r.stem;
    const warn = (r.warnings || []).length ? ` · ⚠ ${r.warnings.join("; ")}` : "";
    card.querySelector(".stem-meta").textContent =
      `${r.notes} notes · ${r.bpm} BPM · ${r.key}${warn}`;

    const nav = card.querySelector(".stem-downloads");
    for (const [ext, url] of Object.entries(r.files)) {
      const a = document.createElement("a");
      a.href = withToken(url);   // <a> can't send headers
      a.textContent = `.${ext}`;
      a.setAttribute("download", "");
      nav.appendChild(a);
    }

    card.querySelector(".asciitab").textContent = r.ascii;

    const atEl = card.querySelector(".alphatab");
    // grab the button before appendChild empties the template fragment
    const playBtn = card.querySelector(".stem-play");
    resultsEl.appendChild(card);

    // alphaTab renders staff + tab from the .gp5, if it was built.
    // The synth + ~2 MB soundfont load lazily on the first Play click:
    // booting one per card upfront froze the results panel.
    if (r.files.gp5 && window.alphaTab) {
      atEl.hidden = false;
      const makeApi = (withPlayer) => new alphaTab.AlphaTabApi(atEl, {
        file: withToken(r.files.gp5),
        display: { staveProfile: "ScoreTab" },
        player: withPlayer ? {
          enablePlayer: true,
          soundFont: "https://cdn.jsdelivr.net/npm/@coderline/alphatab@1.4.0/dist/soundfont/sonivox.sf2",
          scrollElement: atEl,
        } : { enablePlayer: false },
      });
      try {
        let api = makeApi(false);
        let playerArmed = false;
        playBtn.hidden = false;
        playBtn.disabled = false;
        playBtn.addEventListener("click", () => {
          if (playerArmed) { api.playPause(); return; }
          playerArmed = true;
          playBtn.disabled = true;
          playBtn.textContent = "… loading";
          api.destroy();
          api = makeApi(true);
          api.playerReady.on(() => { playBtn.disabled = false; api.playPause(); });
          api.playerStateChanged.on((e) => {
            playBtn.textContent =
              e.state === alphaTab.synth.PlayerState.Playing ? "⏸ Pause" : "▶ Play";
          });
          // async load failures (offline, blocked CDN soundfont) would
          // leave the button disabled with cursor:wait forever
          api.error.on(() => {
            if (playBtn.disabled) playBtn.hidden = true;
          });
        });
      } catch (e) {
        atEl.hidden = true;   // the ASCII tab remains
        playBtn.hidden = true;
      }
    }
  }
}
