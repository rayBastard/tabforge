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
    const res = await fetch(`/api/jobs?${params}`, { method: "POST", body: form });
    if (!res.ok) throw new Error(await res.text());
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
    const res = await fetch(`/api/jobs/${id}`);
    if (!res.ok) throw new Error(`status request failed (HTTP ${res.status})`);
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
      a.href = url;
      a.textContent = `.${ext}`;
      a.setAttribute("download", "");
      nav.appendChild(a);
    }

    card.querySelector(".asciitab").textContent = r.ascii;

    const atEl = card.querySelector(".alphatab");
    // grab the button before appendChild empties the template fragment
    const playBtn = card.querySelector(".stem-play");
    resultsEl.appendChild(card);

    // alphaTab renders staff + tab from the .gp5, if it was built
    if (r.files.gp5 && window.alphaTab) {
      atEl.hidden = false;
      try {
        const api = new alphaTab.AlphaTabApi(atEl, {
          file: r.files.gp5,
          display: { staveProfile: "ScoreTab" },
          player: {
            enablePlayer: true,
            soundFont: "https://cdn.jsdelivr.net/npm/@coderline/alphatab@1.4.0/dist/soundfont/sonivox.sf2",
            scrollElement: atEl,
          },
        });
        playBtn.hidden = false;                  // enabled once the synth is ready
        api.playerReady.on(() => { playBtn.disabled = false; });
        api.playerStateChanged.on((e) => {
          playBtn.textContent =
            e.state === alphaTab.synth.PlayerState.Playing ? "⏸ Pause" : "▶ Play";
        });
        playBtn.addEventListener("click", () => api.playPause());
      } catch (e) {
        atEl.hidden = true;   // the ASCII tab remains
        playBtn.hidden = true;
      }
    }
  }
}
