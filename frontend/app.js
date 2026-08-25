/* TabForge frontend: upload -> poll the job -> render the tabs. */
"use strict";

const $ = (sel) => document.querySelector(sel);

const fileInput = $("#file");
const drop = $("#drop");
const goBtn = $("#go");
const neck = $("#neck");
const logEl = $("#log");
const resultsEl = $("#results");

/* ---------- two screens: Start (funnel) and Project (score) ---------- */

function showScreen(name) {
  $("#screenStart").hidden = name !== "start";
  $("#screenProject").hidden = name !== "project";
  window.scrollTo(0, 0);
}

$("#backBtn").addEventListener("click", () => {
  if (backing.audio) {                 // leaving the project stops the mix
    backing.audio.pause();
    $("#backingPlay").textContent = "♫ backing";
  }
  showScreen("start");
});

let pickedFile = null;

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

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
  currentJobId = null;                 // a new file starts a new job
  $("#instruments").hidden = true;
  $("#splitRow").hidden = true;
  goBtn.textContent = "Analyze track";
  $("#fileName").textContent = f.name;
  goBtn.disabled = false;
}

/* ---------- the two-step flow: analyze, choose, transcribe ---------- */

let currentJobId = null;               // set once the track is analyzed
let activeJobId = null;                // the job being worked on right now

goBtn.addEventListener("click", () => {
  if (currentJobId) startTranscribe();
  else startAnalyze();
});

/* ---------- the Stop button: cancel a running analyze/transcribe ----- */

const stopBtn = $("#stopBtn");

function showStop(on) {
  stopBtn.hidden = !on;
  stopBtn.disabled = false;
  stopBtn.textContent = "✕ stop";
}

stopBtn.addEventListener("click", async () => {
  if (!activeJobId) return;
  stopBtn.disabled = true;
  stopBtn.textContent = "stopping…";
  try {
    const res = await apiFetch(`/api/jobs/${activeJobId}/cancel`,
                               { method: "POST" });
    if (!res.ok) throw new Error(await errorDetail(res));
    // the poll loop notices the job land in canceled/analyzed
  } catch (err) {
    setLog(`Failed to stop: ${err.message}`, true);
    showStop(true);
  }
});

function resetAfterCancel() {
  neck.classList.remove("playing");
  showStop(false);
  markStage(null);
  currentJobId = null;
  activeJobId = null;
  $("#instruments").hidden = true;
  $("#splitRow").hidden = true;
  goBtn.textContent = "Analyze track";
  goBtn.disabled = !pickedFile;
  setLog("Stopped. Press Analyze to start again.");
}

let serverLimits = null;               // fetched once, best-effort

async function fetchLimits() {
  if (serverLimits) return serverLimits;
  try {
    const res = await apiFetch("/api/limits");
    if (res.ok) serverLimits = await res.json();
  } catch { /* the server still enforces its own limit */ }
  return serverLimits;
}

async function startAnalyze() {
  if (!pickedFile) return;
  // say no BEFORE uploading tens of megabytes, and say how much fits
  const limits = await fetchLimits();
  if (limits && pickedFile.size > limits.max_upload_mb * 1e6) {
    setLog(`This file is ${Math.round(pickedFile.size / 1e6)} MB — ` +
           `the server accepts up to ${limits.max_upload_mb} MB.`, true);
    neck.hidden = false;
    return;
  }
  goBtn.disabled = true;
  resultsEl.innerHTML = "";
  neck.hidden = false;
  neck.classList.add("playing");
  showStop(true);
  setLog("Uploading the file for processing…");
  markStage(null);

  const form = new FormData();
  form.append("file", pickedFile);
  try {
    const res = await apiFetch("/api/jobs", { method: "POST", body: form });
    if (!res.ok) throw new Error(await errorDetail(res));
    const { id } = await res.json();
    activeJobId = id;
    poll(id);
  } catch (err) {
    fail(`Failed to start: ${err.message}`);
  }
}

async function startTranscribe() {
  const picked = [...document.querySelectorAll("#instruments input:checked")]
    .map((c) => c.value);
  if (!picked.length) { setLog("Pick at least one instrument.", true); return; }
  goBtn.disabled = true;
  resultsEl.innerHTML = "";
  neck.classList.add("playing");
  showStop(true);
  const tuningSel = $("#instTuning");
  try {
    const res = await apiFetch(`/api/jobs/${currentJobId}/transcribe`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        stems: picked,
        tuning: tuningSel ? tuningSel.value : "standard",
        split_guitars: $("#splitGuitars").checked,
      }),
    });
    if (!res.ok) throw new Error(await errorDetail(res));
    activeJobId = currentJobId;
    poll(currentJobId);
  } catch (err) {
    fail(`Failed to start: ${err.message}`);
  }
}

/* ---------- instrument cards from the analyze step ---------- */

const NOTE_NAMES = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"];
const noteName = (m) => m == null ? "?" : NOTE_NAMES[m % 12] + (Math.floor(m / 12) - 1);
const GUITAR_TUNINGS = [
  ["standard", "Standard (E A D G B E)"],
  ["drop_d", "Drop D"],
  ["eb_standard", "Half step down (E♭)"],
  ["dadgad", "DADGAD"],
  ["open_g", "Open G"],
];

function showInstruments(job) {
  currentJobId = job.id;
  const box = $("#instruments");
  box.innerHTML = "<legend>Instruments in this track</legend>";
  let hasGuitar = false;
  for (const a of job.analysis) {
    const row = document.createElement("label");
    row.className = "inst " + a.status;
    const checked = a.status === "found" ? "checked" : "";
    const disabled = a.status === "absent" ? "disabled" : "";
    const range = a.stem === "drums"                 // unpitched: no range
      ? (a.notes ? `${a.notes} hits` : "—")
      : a.min_pitch != null
        ? `${noteName(a.min_pitch)}–${noteName(a.max_pitch)}` : "—";
    row.innerHTML =
      `<input type="checkbox" value="${a.stem}" ${checked} ${disabled}>
       <span class="inst-name">${STEM_NAMES[a.stem] || a.stem}</span>
       <span class="inst-status ${a.status}">${a.status}</span>
       <span class="inst-range">${range}</span>`;
    box.appendChild(row);
    if (a.stem === "guitar" && a.status !== "absent") hasGuitar = true;
    if (a.stem === "guitar" && a.suggested_tuning) {
      const sel = document.createElement("select");
      sel.id = "instTuning";
      for (const [value, label] of GUITAR_TUNINGS) {
        const o = document.createElement("option");
        o.value = value; o.textContent = label;
        o.selected = value === a.suggested_tuning;
        sel.appendChild(o);
      }
      const wrap = document.createElement("div");
      wrap.className = "inst-tuning";
      wrap.append("suggested tuning: ", sel);
      box.appendChild(wrap);
    }
    if (a.stem === "bass" && a.suggested_tuning === "bass_5") {
      const note = document.createElement("p");
      note.className = "inst-note";
      note.textContent =
        "bass goes below E1 — a 5-string would fit better";
      box.appendChild(note);
    }
  }
  box.hidden = false;
  $("#splitRow").hidden = !hasGuitar;
  neck.classList.remove("playing");
  showStop(false);
  setLog("Analyzed. Pick the instruments and press Transcribe.");
  goBtn.textContent = "Transcribe to tab";
  goBtn.disabled = false;
}

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

    if (job.status === "analyzed") return showInstruments(job);
    if (job.status === "done") return finish(job);
    if (job.status === "canceled") return resetAfterCancel();
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
  showStop(false);
  setLog(msg, true);
  goBtn.disabled = false;
}

/* ---------- results ---------- */

const STEM_NAMES = { guitar: "Guitar", bass: "Bass", vocals: "Vocals",
                     piano: "Keys", drums: "Drums", other: "Other",
                     mix: "Full mix",
                     guitar_lead: "Guitar · Lead",
                     guitar_rhythm: "Guitar · Rhythm" };

function finish(job) {
  neck.classList.remove("playing");
  showStop(false);
  markStage("done");
  setLog("Done. Change the selection and transcribe again if you like.");
  goBtn.disabled = false;              // re-transcribe with a new selection

  const backingLink = $("#backingLink");
  if (job.backing) {
    backingLink.href = withToken(job.backing);
    backingLink.hidden = false;
  } else {
    backingLink.hidden = true;
  }
  setupBackingPlayer(job.backing);

  if (!job.results.length) {
    setLog("Processing finished, but no notes were found. Try other stems.", true);
    return;
  }

  // the Project screen header
  $("#projectName").textContent = pickedFile ? pickedFile.name : "project";
  const first = job.results[0];
  $("#projectMeta").textContent = `${first.bpm} BPM · ${first.key}`;

  // instrument list on the left: click scrolls to that part's score;
  // mute/solo arrive with the unified player (task 28)
  const tracklist = $("#tracklist");
  tracklist.innerHTML = "";
  for (const r of job.results) {
    const row = document.createElement("div");
    row.className = "track-row";
    row.setAttribute("role", "button");
    row.tabIndex = 0;
    row.innerHTML =
      `<span class="track-name">${STEM_NAMES[r.stem] || r.stem}</span>
       <span class="track-notes">${r.notes}</span>
       <button class="track-toggle" data-what="mute" title="mute">M</button>
       <button class="track-toggle" data-what="solo" title="solo">S</button>`;
    row.addEventListener("click", (e) => {
      const toggle = e.target.closest(".track-toggle");
      if (toggle) {
        toggleTrack(r.stem, toggle.dataset.what, toggle);
        return;
      }
      const card = document.getElementById(`card-${r.stem}`);
      if (card) card.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    tracklist.appendChild(row);
  }

  // switch BEFORE rendering: alphaTab measures its container, and a
  // hidden one has zero width
  showScreen("project");

  const tpl = $("#stemCard");
  for (const r of job.results) {
    const card = tpl.content.cloneNode(true);
    card.querySelector(".stem").id = `card-${r.stem}`;
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

    resultsEl.appendChild(card);
  }

  initUnifiedScore(job);
}

/* ---------- the unified project player (one alphaTab, all tracks) ---- */

const unified = { api: null, armed: false, mixer: new Map(), view: "all" };
window._tf = unified;                 // exposed for tests

function renderView() {
  // one instrument per tab, or everything at once — display only:
  // every track still SOUNDS, mute/solo stay in charge of the mix
  const api = unified.api;
  if (!api || !api.score) return;
  const picked = unified.view === "all" ? api.score.tracks
    : api.score.tracks.filter((t) => t.name === unified.view);
  api.renderTracks(picked.length ? picked : api.score.tracks);
  document.querySelectorAll("#scoreTabs button").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === unified.view));
}

function buildScoreTabs(job) {
  const nav = $("#scoreTabs");
  nav.innerHTML = "";
  const views = [["all", "All"],
                 ...job.results.map((r) => [r.stem, STEM_NAMES[r.stem] || r.stem])];
  if (!job.results.some((r) => r.stem === unified.view)) unified.view = "all";
  for (const [view, label] of views) {
    const b = document.createElement("button");
    b.dataset.view = view;
    b.textContent = label;
    b.addEventListener("click", () => { unified.view = view; renderView(); });
    nav.appendChild(b);
  }
  nav.hidden = job.results.length < 2;
}

function initUnifiedScore(job) {
  const atEl = $("#unifiedScore");
  const playBtn = $("#transportPlay");
  const posEl = $("#transportPos");
  if (!job.song || !window.alphaTab) {
    atEl.hidden = true;
    playBtn.disabled = true;
    return;
  }
  // instrument name -> tablature? (piano/vocals are notation-only)
  const tabByName = Object.fromEntries(
    job.results.map((r) => [r.stem, r.tablature !== false]));
  unified.mixer.clear();
  buildScoreTabs(job);

  const makeApi = (withPlayer) => {
    const api = new alphaTab.AlphaTabApi(atEl, {
      file: withToken(job.song),
      player: withPlayer ? {
        enablePlayer: true,
        soundFont: "https://cdn.jsdelivr.net/npm/@coderline/alphatab@1.4.0/dist/soundfont/sonivox.sf2",
        scrollElement: $("#scoreMain"),
      } : { enablePlayer: false },
    });
    api.scoreLoaded.on((score) => {
      // notation-only tracks lose their tab staff
      for (const t of score.tracks) {
        if (tabByName[t.name] === false) {
          for (const stave of t.staves) {
            stave.showTablature = false;
            stave.showStandardNotation = true;
          }
        }
      }
      posEl.textContent = `${score.masterBars.length} bars`;
      renderView();                     // honors the selected tab
    });
    if (withPlayer) {
      // "where am I": the transport counts bars as playback advances
      api.playedBeatChanged.on((beat) => {
        if (!beat) return;
        posEl.textContent =
          `bar ${beat.voice.bar.index + 1} / ${api.score.masterBars.length}`;
      });
    }
    // the note editor: click a note, pick where it should live
    api.noteMouseDown.on((note) => showNotePopover(note));
    return api;
  };

  atEl.hidden = false;
  try {
    unified.api = makeApi(false);
    unified.armed = false;
    playBtn.disabled = false;
    playBtn.onclick = () => {
      if (unified.armed) { unified.api.playPause(); return; }
      unified.armed = true;
      playBtn.disabled = true;
      playBtn.textContent = "…";
      unified.api.destroy();
      unified.api = makeApi(true);
      unified.api.playerReady.on(() => {
        playBtn.disabled = false;
        applyMixer();
        unified.api.playPause();
      });
      unified.api.playerStateChanged.on((e) => {
        playBtn.textContent =
          e.state === alphaTab.synth.PlayerState.Playing ? "⏸" : "▶";
      });
      unified.api.error.on(() => {
        if (playBtn.disabled) { playBtn.disabled = true; playBtn.textContent = "✕"; }
      });
    };
  } catch (e) {
    atEl.hidden = true;
    playBtn.disabled = true;
  }
}

function applyMixer() {
  const api = unified.api;
  if (!api || !api.score) return;
  for (const t of api.score.tracks) {
    const st = unified.mixer.get(t.name) || {};
    api.changeTrackMute([t], !!st.mute);
    api.changeTrackSolo([t], !!st.solo);
  }
}

/* ---------- backing-track player (play along without downloading) ---- */

const backing = { audio: null, url: null };

function setupBackingPlayer(url) {
  const btn = $("#backingPlay");
  if (!btn) return;
  if (backing.audio && backing.url !== url) {   // re-transcribed: new mix
    backing.audio.pause();
    backing.audio = null;
  }
  backing.url = url;
  if (!url) { btn.hidden = true; return; }
  btn.hidden = false;
  btn.textContent = "♫ backing";
  btn.onclick = () => {
    if (!backing.audio) {
      backing.audio = new Audio(withToken(url));
      backing.audio.addEventListener("ended",
        () => { btn.textContent = "♫ backing"; });
    }
    if (backing.audio.paused) {
      backing.audio.play();
      btn.textContent = "⏸ backing";
    } else {
      backing.audio.pause();
      btn.textContent = "♫ backing";
    }
  };
}

/* ---------- the note editor: click a note, choose its string --------- */

let lastPointer = { x: 200, y: 200 };
document.addEventListener("mousedown",
  (e) => { lastPointer = { x: e.clientX, y: e.clientY }; }, true);

const editor = { lastAction: null };   // for one-step undo

function closePopover() {
  document.querySelector(".note-popover")?.remove();
}

function showNotePopover(note) {
  closePopover();
  const staff = note.beat.voice.bar.staff;
  const track = staff.track;
  const tuning = staff.tuning;                       // index 0 = string 1
  if (!tuning || !tuning.length) return;             // notation-only part
  // drums: pitches are kit voices, there is no string to move a hit to
  if (staff.isPercussion || tuning.every((v) => !v)) return;
  const pitch = note.realValue;
  const qticks = note.beat.absolutePlaybackStart ?? note.beat.playbackStart;

  const pop = document.createElement("div");
  pop.className = "note-popover";
  const title = document.createElement("p");
  title.textContent = `${noteName(pitch)} — where should it live?`;
  pop.appendChild(title);

  const n = tuning.length;
  // alphaTab counts note.string from the LOWEST string; our loop (and
  // the tuning array) go from the highest — mirror before comparing
  const currentS = n - note.string + 1;
  for (let s = 1; s <= n; s++) {
    const fret = pitch - tuning[s - 1];
    if (fret < 0 || fret > 24) continue;
    const b = document.createElement("button");
    b.textContent = `string ${s}, fret ${fret}` +
      (s === currentS ? "  ← now" : "");
    if (s === currentS) b.classList.add("current");
    b.addEventListener("click", () =>
      repin(track.name, qticks, pitch, n - s, b));   // server counts from low E
    pop.appendChild(b);
  }
  const close = document.createElement("button");
  close.textContent = "✕ close";
  close.addEventListener("click", closePopover);
  pop.appendChild(close);

  pop.style.left = Math.min(lastPointer.x, window.innerWidth - 240) + "px";
  pop.style.top = (lastPointer.y + 12) + "px";
  document.body.appendChild(pop);
}

async function repin(part, qticks, pitch, string, btn) {
  if (btn) { btn.disabled = true; btn.textContent += " …"; }
  try {
    const res = await apiFetch(`/api/jobs/${currentJobId}/repin`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ part, qticks, pitch, string }),
    });
    if (!res.ok) throw new Error(await errorDetail(res));
    const data = await res.json();
    editor.lastAction = { part, qticks, pitch, prev: data.prev };
    $("#undoBtn").hidden = false;
    closePopover();
    reloadScore(data.song);
  } catch (err) {
    closePopover();
    setLog(`Repin failed: ${err.message}`, true);
  }
}

function reloadScore(songUrl) {
  if (!unified.api) return;
  // cache-buster: the gp5 on disk changed but the URL did not
  unified.api.load(withToken(songUrl) +
    (songUrl.includes("?") ? "&" : "?") + "v=" + Date.now());
}

$("#undoBtn")?.addEventListener("click", async () => {
  const a = editor.lastAction;
  if (!a) return;
  await repin(a.part, a.qticks, a.pitch, a.prev ?? null, null);
  editor.lastAction = null;
  $("#undoBtn").hidden = true;
});

function toggleTrack(name, what, btn) {
  const st = unified.mixer.get(name) || { mute: false, solo: false };
  st[what] = !st[what];
  unified.mixer.set(name, st);
  btn.classList.toggle("active", st[what]);
  applyMixer();
}
