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
  closePopover();                      // it must never outlive its screen
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

async function openProjectFile() {
  goBtn.disabled = true;
  neck.hidden = false;
  neck.classList.add("playing");
  setLog("Opening the project…");
  const form = new FormData();
  form.append("file", pickedFile);
  try {
    const res = await apiFetch("/api/projects", { method: "POST", body: form });
    if (!res.ok) throw new Error(await errorDetail(res));
    const { id } = await res.json();
    activeJobId = id;
    currentJobId = id;                 // the editor needs the job id
    poll(id);                          // status is already "done"
  } catch (err) {
    fail(`Failed to open: ${err.message}`);
  }
}

async function startAnalyze() {
  if (!pickedFile) return;
  if (pickedFile.name.toLowerCase().endsWith(".tabforge")) {
    return openProjectFile();          // a saved project, not audio
  }
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
        subdivision: parseInt($("#instPrecision")?.value || "2", 10),
        treat: Object.fromEntries(
          [...document.querySelectorAll(".inst-treat")]
            .filter((s) => s.value !== s.dataset.def)
            .map((s) => [s.dataset.stem, s.value])),
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
  ["eb_standard", "Half step down (E♭)"],
  ["drop_d", "Drop D"],
  ["d_standard", "D standard"],
  ["drop_db", "Drop C♯"],
  ["c_standard", "C standard"],
  ["drop_c", "Drop C"],
  ["b_standard", "B standard"],
  ["drop_b", "Drop B"],
  ["drop_bb", "Drop A♯"],
  ["drop_a", "Drop A (6-string)"],
  ["seven_string", "7-string · B standard"],
  ["seven_drop_a", "7-string · Drop A"],
  ["eight_string", "8-string · F♯ standard"],
  ["dadgad", "DADGAD"],
  ["open_g", "Open G"],
];

// what the tagger SHOULD hear per stem; a miss means demucs put
// something else in there and "treat as" deserves a look
const EXPECTED_SOUND = {
  guitar: ["guitar", "banjo", "ukulele", "mandolin"],
  bass: ["bass", "guitar"],
  piano: ["piano", "keyboard", "organ", "harpsichord", "celesta"],
  vocals: ["sing", "choir", "vocal", "speech", "chant", "yodel"],
};
const TREAT_ROLES = [
  ["guitar", "Guitar — tablature"],
  ["piano", "Keys — notation"],
  ["vocals", "Voice — notation"],
];
const DEFAULT_ROLE = { guitar: "guitar", piano: "piano", vocals: "vocals",
                       other: "guitar" };

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

    // what the tagger heard, with a warning when it contradicts the name
    if ((a.sounds_like || []).length && a.status !== "absent") {
      const heard = a.sounds_like[0];
      const expected = EXPECTED_SOUND[a.stem];
      const off = expected
        && !expected.some((k) => heard.toLowerCase().includes(k));
      const p = document.createElement("p");
      p.className = "inst-note" + (off ? "" : " inst-heard");
      p.textContent = off
        ? `⚠ sounds like ${heard.toLowerCase()} — check "treat as" below`
        : `sounds like: ${a.sounds_like.join(", ").toLowerCase()}`;
      box.appendChild(p);
    }
    // role override: how this stem should be WRITTEN
    if (a.stem in DEFAULT_ROLE && a.status !== "absent") {
      const def = DEFAULT_ROLE[a.stem];
      const wrap = document.createElement("div");
      wrap.className = "inst-tuning";
      const sel = document.createElement("select");
      sel.className = "inst-treat";
      sel.dataset.stem = a.stem;
      sel.dataset.def = def;
      for (const [value, label] of TREAT_ROLES) {
        const o = document.createElement("option");
        o.value = value; o.textContent = label;
        o.selected = value === def;
        sel.appendChild(o);
      }
      wrap.append("treat as: ", sel);
      box.appendChild(wrap);
    }

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
  // rhythm precision: eighths are steady, sixteenths catch fast runs
  // but amplify transcription timing noise
  const prec = document.createElement("div");
  prec.className = "inst-tuning";
  prec.innerHTML =
    `rhythm precision: <select id="instPrecision">
       <option value="2" selected>Eighth notes — steady</option>
       <option value="3">Triplets — shuffle feel</option>
       <option value="4">Sixteenths — max detail</option>
     </select>`;
  box.appendChild(prec);

  box.hidden = false;
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
                     piano: "Keys", piano_left: "Keys · left hand",
                     drums: "Drums", other: "Other",
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

  const saveLink = $("#saveLink");
  saveLink.href = withToken(`/api/jobs/${job.id}/project`);
  saveLink.hidden = false;

  if (!job.results.length) {
    setLog("Processing finished, but no notes were found. Try other stems.", true);
    return;
  }

  // the Project screen header
  $("#projectName").textContent = pickedFile ? pickedFile.name : "project";
  const first = job.results[0];
  $("#projectMeta").textContent = `${first.bpm} BPM · ${first.key}`;

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

const unified = { api: null, armed: false, mixer: new Map(), view: null,
                  tabByName: {} };
window._tf = unified;                 // exposed for tests

// a grand staff is two TRACKS but one instrument: group them everywhere
function groupOf(stem) {
  return stem === "piano_left" ? "piano" : stem;
}

function renderView() {
  // one instrument per tab — display only: every track still SOUNDS,
  // mute/solo stay in charge of the mix
  const api = unified.api;
  if (!api || !api.score) return;
  const picked = api.score.tracks.filter(
    (t) => groupOf(t.name) === unified.view);
  api.renderTracks(picked.length ? picked : api.score.tracks);
  document.querySelectorAll("#scoreTabs .tab-chip").forEach((c) =>
    c.classList.toggle("active", c.dataset.view === unified.view));
  rebuildVirtual();
}

function buildScoreTabs(job) {
  // one chip per instrument at the bottom: the name switches the view,
  // M/S mute and solo THAT track right on the tab. Grouped parts
  // (the piano's two hands) share a single chip.
  const nav = $("#scoreTabs");
  nav.innerHTML = "";
  const groups = [];
  for (const r of job.results) {
    const g = groupOf(r.stem);
    const known = groups.find((x) => x.group === g);
    if (known) known.notes += r.notes;
    else groups.push({ group: g, stem: g, notes: r.notes });
  }
  if (!groups.some((r) => r.stem === unified.view)) {
    unified.view = groups[0]?.stem ?? null;
  }
  for (const r of groups) {
    const chip = document.createElement("div");
    chip.className = "tab-chip";
    chip.dataset.view = r.stem;
    const name = document.createElement("button");
    name.className = "tab-name";
    name.textContent = STEM_NAMES[r.stem] || r.stem;
    name.title = `${r.notes} notes`;
    name.addEventListener("click", () => { unified.view = r.stem; renderView(); });
    chip.appendChild(name);
    for (const what of ["mute", "solo"]) {
      const b = document.createElement("button");
      b.className = "track-toggle";
      b.dataset.what = what;
      b.title = what;
      b.textContent = what === "mute" ? "M" : "S";
      b.addEventListener("click", () => toggleTrack(r.stem, what, b));
      chip.appendChild(b);
    }
    nav.appendChild(chip);
  }
  nav.hidden = !job.results.length;
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
  unified.tabByName = Object.fromEntries(
    job.results.map((r) => [r.stem, r.tablature !== false]));
  const tabByName = unified.tabByName;
  unified.mixer.clear();
  buildScoreTabs(job);

  const makeApi = (withPlayer) => {
    const api = new alphaTab.AlphaTabApi(atEl, {
      file: withToken(job.song),
      // per-note hit boxes are OFF by default — without them
      // noteMouseDown never fires and clicking a tab digit does nothing
      core: { includeNoteBounds: true },
      player: withPlayer ? {
        enablePlayer: true,
        soundFont: "https://cdn.jsdelivr.net/npm/@coderline/alphatab@1.4.0/dist/soundfont/sonivox.sf2",
        scrollElement: $("#scoreMain"),
      } : { enablePlayer: false },
    });
    api.scoreLoaded.on((score) => {
      // fretted instruments read TAB, keys/vocals read notation —
      // nobody needs both staves at once
      for (const t of score.tracks) {
        const percussion = t.staves[0]?.isPercussion;
        for (const stave of t.staves) {
          if (percussion) {
            stave.showTablature = false;
            stave.showStandardNotation = true;
          } else if (tabByName[t.name] === false) {
            stave.showTablature = false;
            stave.showStandardNotation = true;
          } else {
            stave.showTablature = true;
            stave.showStandardNotation = false;
          }
        }
        // the left hand of the grand staff reads in bass clef
        if (t.name === "piano_left") {
          for (const stave of t.staves) {
            for (const b of stave.bars) b.clef = alphaTab.model.Clef.F4;
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
      // the virtual instrument lights the notes of the ACTIVE tab
      api.activeBeatsChanged.on((args) => {
        if (!virtual.track) return;
        const group = groupOf(virtual.track.name);
        const notes = [];
        for (const beat of args.activeBeats || []) {
          if (groupOf(beat.voice.bar.staff.track.name) !== group) continue;
          for (const note of beat.notes) notes.push(note);
        }
        virtualLiveHighlight(notes);
      });
    }
    // clicking ANYWHERE in the score shows that beat's notes on the
    // virtual instrument — no need to catch them during playback
    api.beatMouseDown.on((beat) => {
      if (beat && virtual.track
          && groupOf(beat.voice.bar.staff.track.name)
             === groupOf(virtual.track.name)) {
        virtualLiveHighlight(beat.notes);
      }
    });
    // the note editor: click a note, pick where it should live
    api.noteMouseDown.on((note) => showNotePopover(note));
    return api;
  };

  atEl.hidden = false;
  try {
    // the player arms right away (soundfont loads in the background):
    // clicking the score then seeks and shows the bar cursor BEFORE
    // the first play — no need to start the track to see where you are
    unified.api = makeApi(true);
    unified.armed = false;
    playBtn.disabled = true;
    playBtn.textContent = "…";
    unified.api.playerReady.on(() => {
      unified.armed = true;
      playBtn.disabled = false;
      playBtn.textContent = "▶";
      applyMixer();
    });
    unified.api.playerStateChanged.on((e) => {
      playBtn.textContent =
        e.state === alphaTab.synth.PlayerState.Playing ? "⏸" : "▶";
    });
    unified.api.error.on(() => {
      if (!unified.armed) { playBtn.disabled = true; playBtn.textContent = "✕"; }
    });
    playBtn.onclick = () => {
      if (unified.armed) unified.api.playPause();
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

/* ---------- space = play/pause on the project screen ----------------- */

document.addEventListener("keydown", (e) => {
  if (e.code !== "Space") return;
  if ($("#screenProject").hidden) return;
  // don't steal space from form fields (or let it click a focused button)
  if (e.target.matches("input, select, textarea, [contenteditable]")) return;
  e.preventDefault();                    // the page would scroll otherwise
  $("#transportPlay").click();           // first press arms the synth too
});

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

/* ---------- the virtual instrument bar ------------------------------- */
/* Fretboard for fretted tracks, a keyboard for keys/vocals, pads for
   drums — following the active tab. Playback lights the current notes;
   on the fretboard a clicked score note also shows its alternative
   positions, and clicking one re-pins the note (same math as the
   popover, second entry point into repin). */

const SVG_NS = "http://www.w3.org/2000/svg";
const MAX_VFRET = 24;      // the full neck: a high-fret alternative
                           // must be clickable, not silently dropped

const virtual = {
  mode: null, track: null, tuning: [],
  fretEls: [], keyEls: {}, padEls: [],
  lit: [], editing: null, hits: 0,       // hits: exposed for tests
};

const DRUM_PADS = [
  ["Kick", [35, 36]], ["Snare", [37, 38, 40]],
  ["Hi-hat", [42, 44]], ["Open hat", [46]],
  ["Toms", [41, 43, 45, 47, 48, 50]],
  ["Ride", [51, 53, 59]], ["Crash", [49, 52, 55, 57]],
];

function svgEl(name, attrs) {
  const el = document.createElementNS(SVG_NS, name);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

function currentViewTrack() {
  const api = unified.api;
  if (!api || !api.score) return null;
  return api.score.tracks.find((t) => groupOf(t.name) === unified.view)
    || api.score.tracks[0];
}

function rebuildVirtual() {
  const bar = $("#virtualBar");
  if (!bar) return;
  const track = currentViewTrack();
  if (!track) { bar.hidden = true; return; }
  const staff = track.staves[0];
  const tuning = (staff.tuning || []).slice();  // index 0 = string 1 (high)
  virtual.track = track;
  virtual.tuning = tuning;
  virtual.editing = null;
  virtual.lit = [];
  virtual.labelLayer = null;
  virtual.chordText = null;
  if (staff.isPercussion || (tuning.length && tuning.every((v) => !v))) {
    virtual.mode = "drums";
    buildDrumPads(bar);
  } else if (unified.tabByName[track.name] === false || !tuning.length) {
    virtual.mode = "keys";                      // no strings to point at
    buildKeyboard(bar);
  } else {
    virtual.mode = "frets";
    buildFretboard(bar, tuning);
  }
  bar.hidden = false;
}

function buildFretboard(bar, tuning) {
  const n = tuning.length;
  const H = 20 + n * 16, W = 1000;
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}` });
  const colW = (W - 60) / MAX_VFRET;
  const fx = (f) => f === 0 ? 20 : 40 + (f - 0.5) * colW;
  const sy = (row) => 12 + row * 16;
  svg.appendChild(svgEl("line", { x1: 40, x2: 40, y1: sy(0), y2: sy(n - 1),
                                  class: "v-nut" }));
  for (let f = 1; f <= MAX_VFRET; f++) {
    svg.appendChild(svgEl("line", { x1: 40 + f * colW, x2: 40 + f * colW,
                                    y1: sy(0), y2: sy(n - 1),
                                    class: "v-fret" }));
  }
  for (const f of [3, 5, 7, 9, 12, 15, 17, 19, 21, 24]) {
    svg.appendChild(svgEl("circle", { cx: fx(f), cy: sy(n - 1) + 12, r: 3,
                                      class: "v-marker" }));
    const t = svgEl("text", { x: fx(f) - 3, y: H - 1, class: "v-label" });
    t.textContent = f;
    svg.appendChild(t);
  }
  virtual.fretEls = [];
  for (let row = 0; row < n; row++) {           // row 0 = highest string
    svg.appendChild(svgEl("line", { x1: 20, x2: W - 5,
                                    y1: sy(row), y2: sy(row),
                                    class: "v-string" }));
    const dots = [];
    for (let f = 0; f <= MAX_VFRET; f++) {
      const c = svgEl("circle", { cx: fx(f), cy: sy(row), r: 6,
                                  class: "v-dot" });
      c.dataset.s = row + 1;                    // string 1..n from the top
      svg.appendChild(c);
      dots.push(c);
    }
    virtual.fretEls.push(dots);
  }
  virtual.labelLayer = svgEl("g", {});
  svg.appendChild(virtual.labelLayer);
  virtual.chordText = svgEl("text", { x: W - 6, y: 12, class: "v-chord" });
  svg.appendChild(virtual.chordText);
  svg.addEventListener("click", (e) => {
    const alt = e.target.closest(".v-dot.alt");
    if (alt && virtual.editing) {
      const { part, qticks, pitch, n: nStr } = virtual.editing;
      repin(part, qticks, pitch, nStr - parseInt(alt.dataset.s, 10), null);
      closePopover();
      return;
    }
    // a LIT playback dot is the natural editing entry: pause where the
    // note plays, click the dot itself
    const live = e.target.closest(".v-dot.live");
    if (live && live._tfNote) showNotePopover(live._tfNote);
  });
  bar.replaceChildren(svg);
}

const NOTE_LETTERS = ["C", "C♯", "D", "D♯", "E", "F",
                      "F♯", "G", "G♯", "A", "A♯", "B"];

// chord templates, roughly by how specific they are
const CHORD_SHAPES = [
  ["7", [0, 4, 7, 10]], ["m7", [0, 3, 7, 10]], ["maj7", [0, 4, 7, 11]],
  ["", [0, 4, 7]], ["m", [0, 3, 7]], ["dim", [0, 3, 6]], ["aug", [0, 4, 8]],
  ["sus4", [0, 5, 7]], ["sus2", [0, 2, 7]], ["5", [0, 7]],
];

function chordName(pitches) {
  if (!pitches.length) return "";
  const pcs = [...new Set(pitches.map((p) => p % 12))];
  if (pcs.length < 2) return noteName(Math.min(...pitches));
  const bass = Math.min(...pitches) % 12;
  const roots = [bass, ...pcs.filter((r) => r !== bass)];
  for (const exact of [true, false]) {
    for (const root of roots) {
      for (const [suffix, shape] of CHORD_SHAPES) {
        const set = new Set(shape.map((i) => (root + i) % 12));
        if (!pcs.every((pc) => set.has(pc))) continue;
        if (exact && set.size !== pcs.length) continue;
        const name = NOTE_LETTERS[root] + suffix;
        return root === bass ? name : `${name}/${NOTE_LETTERS[bass]}`;
      }
    }
  }
  return NOTE_LETTERS[bass] + "…";
}

function buildKeyboard(bar) {
  const LO = 21, HI = 108;         // the full classical piano: A0..C8
  const isBlack = (m) => [1, 3, 6, 8, 10].includes(m % 12);
  const whites = [];
  for (let m = LO; m <= HI; m++) if (!isBlack(m)) whites.push(m);
  const W = 1000, H = 110, kw = W / whites.length;
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}` });
  virtual.keyEls = {};
  whites.forEach((m, i) => {
    const r = svgEl("rect", { x: i * kw, y: 0, width: kw, height: H,
                              class: "v-white" });
    svg.appendChild(r);
    virtual.keyEls[m] = r;
  });
  whites.forEach((m, i) => {                     // blacks overlay
    if (m + 1 <= HI && isBlack(m + 1)) {
      const r = svgEl("rect", { x: (i + 0.65) * kw, y: 0,
                                width: kw * 0.7, height: H * 0.6,
                                class: "v-black" });
      svg.appendChild(r);
      virtual.keyEls[m + 1] = r;
    }
  });
  virtual.chordText = svgEl("text", { x: W - 6, y: 14, class: "v-chord" });
  svg.appendChild(virtual.chordText);
  bar.replaceChildren(svg);
}

function buildDrumPads(bar) {
  const W = 1000, H = 110, pw = W / DRUM_PADS.length;
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}` });
  virtual.padEls = [];
  DRUM_PADS.forEach(([label], i) => {
    const r = svgEl("rect", { x: i * pw + 8, y: 8, width: pw - 16,
                              height: H - 16, class: "v-pad" });
    svg.appendChild(r);
    const t = svgEl("text", { x: i * pw + pw / 2, y: H / 2 + 4,
                              class: "v-pad-label" });
    t.textContent = label;
    svg.appendChild(t);
    virtual.padEls.push(r);
  });
  bar.replaceChildren(svg);
}

function virtualLiveHighlight(notes) {
  for (const el of virtual.lit) {
    el.classList.remove("live");
    el._tfNote = null;
  }
  virtual.lit = [];
  if (virtual.labelLayer) virtual.labelLayer.innerHTML = "";
  for (const note of notes) {
    let el = null;
    if (virtual.mode === "frets") {
      const row = virtual.tuning.length - note.string;   // alphaTab: 1=low
      const fret = note.fret;
      el = virtual.fretEls[row]?.[Math.min(fret, MAX_VFRET)];
      if (el && virtual.labelLayer) {   // note letter above the dot
        const t = svgEl("text", { x: el.getAttribute("cx"),
                                  y: el.getAttribute("cy") - 8,
                                  class: "v-note-name" });
        t.textContent = NOTE_LETTERS[note.realValue % 12];
        virtual.labelLayer.appendChild(t);
      }
    } else if (virtual.mode === "keys") {
      el = virtual.keyEls[note.realValue];
    } else {
      const gm = note.fret >= 0 ? note.fret : note.realValue;
      const idx = DRUM_PADS.findIndex(([, gms]) => gms.includes(gm));
      el = virtual.padEls[idx >= 0 ? idx : 3];
    }
    if (el) {
      el.classList.add("live");
      el._tfNote = note;                // click the lit dot to edit it
      virtual.lit.push(el);
    }
  }
  if (virtual.chordText) {              // name what is sounding
    virtual.chordText.textContent =
      chordName(notes.map((n) => n.realValue));
  }
  if (notes.length) virtual.hits += 1;
}

function virtualShowAlternatives(trackName, tuning, pitch, currentS, qticks) {
  if (virtual.mode !== "frets" || virtual.track?.name !== trackName) return;
  virtualClearEditing();
  const n = tuning.length;
  virtual.editing = { part: trackName, qticks, pitch, n };
  for (let s = 1; s <= n; s++) {
    const fret = pitch - tuning[s - 1];
    if (fret < 0 || fret > MAX_VFRET) continue;
    const el = virtual.fretEls[s - 1]?.[fret];
    if (el) el.classList.add(s === currentS ? "cur" : "alt");
  }
}

function virtualClearEditing() {
  virtual.editing = null;
  document.querySelectorAll("#virtualBar .v-dot.alt, #virtualBar .v-dot.cur")
    .forEach((el) => el.classList.remove("alt", "cur"));
}

/* ---------- the note editor: click a note, choose its string --------- */

let lastPointer = { x: 200, y: 200 };
document.addEventListener("mousedown",
  (e) => { lastPointer = { x: e.clientX, y: e.clientY }; }, true);

const editor = { lastAction: null };   // for one-step undo

function closePopover() {
  document.querySelector(".note-popover")?.remove();
  virtualClearEditing();
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
  // second entry point: the same alternatives light up on the fretboard
  virtualShowAlternatives(track.name, tuning, pitch, currentS, qticks);
  let alternatives = 0;
  for (let s = 1; s <= n; s++) {
    const fret = pitch - tuning[s - 1];
    if (fret < 0 || fret > 24) continue;
    const b = document.createElement("button");
    b.textContent = `string ${s}, fret ${fret}` +
      (s === currentS ? "  ← now" : "");
    if (s === currentS) b.classList.add("current");
    else alternatives += 1;
    b.addEventListener("click", () =>
      repin(track.name, qticks, pitch, n - s, b));   // server counts from low E
    pop.appendChild(b);
  }
  if (!alternatives) {           // honesty beats a silent dead end
    const p = document.createElement("p");
    p.className = "popover-note";
    p.textContent = "the only playable position in this tuning";
    pop.appendChild(p);
  }
  const x = document.createElement("button");
  x.className = "popover-x";
  x.textContent = "✕";
  x.title = "Close (Esc)";
  x.addEventListener("click", closePopover);
  pop.appendChild(x);

  // docked at the right edge below the virtual bar: it must never
  // cover the clicked note or the fretboard alternatives
  const barBox = $("#virtualBar")?.getBoundingClientRect();
  pop.style.right = "20px";
  pop.style.left = "auto";
  pop.style.top = ((barBox ? barBox.bottom : 70) + 12) + "px";
  document.body.appendChild(pop);
}

// the popover must never outlive its context
document.addEventListener("keydown", (e) => {
  if (e.code === "Escape") closePopover();
});
document.addEventListener("mousedown", (e) => {
  if (!document.querySelector(".note-popover")) return;
  // clicks inside the popover or on the fretboard (picking an
  // alternative) keep it; anything else dismisses. Capture phase, so a
  // click on ANOTHER note closes this popover before opening its own.
  if (e.target.closest(".note-popover") || e.target.closest("#virtualBar")) return;
  closePopover();
}, true);

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
    // the pin is applied — keep it or put the note back
    $("#undoBtn").hidden = false;
    $("#approveBtn").hidden = false;
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
  $("#approveBtn").hidden = true;
});

$("#approveBtn")?.addEventListener("click", () => {
  // the pin is already saved server-side — approving just settles it
  editor.lastAction = null;
  $("#undoBtn").hidden = true;
  $("#approveBtn").hidden = true;
});

function toggleTrack(name, what, btn) {
  // `name` is a GROUP: the Keys chip must mute both piano hands
  const members = unified.api?.score
    ? unified.api.score.tracks.filter((t) => groupOf(t.name) === name)
        .map((t) => t.name)
    : [name];
  let on = false;
  for (const member of members.length ? members : [name]) {
    const st = unified.mixer.get(member) || { mute: false, solo: false };
    st[what] = !st[what];
    on = st[what];
    unified.mixer.set(member, st);
  }
  btn.classList.toggle("active", on);
  applyMixer();
}
