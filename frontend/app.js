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

// optional-backend rows appear only when the server has the installs
(async () => {
  const limits = await fetchLimits();
  if (limits && limits.mt3_available) $("#mt3Row").hidden = false;
  if (limits && limits.lyrics_available) $("#lyricsRow").hidden = false;
})();

// solo mode and HQ separation are mutually exclusive: one says "don't
// separate at all", the other "separate better"
$("#soloMode")?.addEventListener("change", (e) => {
  if (e.target.checked) $("#hqSeparation").checked = false;
});
$("#hqSeparation")?.addEventListener("change", (e) => {
  if (e.target.checked) $("#soloMode").checked = false;
});

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
  form.append("separator",
              $("#hqSeparation")?.checked ? "roformer" : "demucs");
  form.append("use_mt3", $("#mt3Arbiter")?.checked ? "1" : "0");
  form.append("solo", $("#soloMode")?.checked ? "1" : "0");
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
        tempo_scale: parseFloat($("#instTempoScale")?.value || "1"),
        guitar_engine: $("#instGuitarEngine")?.value || "auto",
        with_chords: $("#withChords")?.checked !== false,
        with_lyrics: $("#withLyrics")?.checked !== false,
        lyrics_lang: $("#lyricsLang")?.value || null,
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
  ["dadgad", "DADGAD"],
  ["open_g", "Open G"],
];

// what the tagger SHOULD hear per stem; a miss means demucs put
// something else in there
const EXPECTED_SOUND = {
  guitar: ["guitar", "banjo", "ukulele", "mandolin"],
  bass: ["bass", "guitar"],
  piano: ["piano", "keyboard", "organ", "harpsichord", "celesta"],
  vocals: ["sing", "choir", "vocal", "speech", "chant", "yodel"],
};

function showInstruments(job) {
  currentJobId = job.id;
  const box = $("#instruments");
  box.innerHTML = "<legend>Instruments in this track</legend>";
  let hasGuitar = false;
  for (const a of job.analysis) {
    const row = document.createElement("label");
    row.className = "inst " + a.status;
    // The MT3 arbiter's verdict refines the RMS status: a phantom card
    // (energetic stem the arbiter can't confirm) starts UNCHECKED but
    // stays clickable; "uncertain" (arbiter blind, stem sounds real)
    // stays checked. Truly silent stems remain disabled as before.
    let checked = a.status === "found" ? "checked" : "";
    if (a.verdict === "absent") checked = "";
    else if (a.verdict === "found" || a.verdict === "uncertain")
      checked = "checked";
    const disabled = a.status === "absent" ? "disabled" : "";
    const VERDICT_BADGE = {
      found: ["✓ arbiter: heard", "found"],
      absent: ["✗ arbiter: not heard", "absent"],
      uncertain: ["? arbiter: unsure", "quiet"],
    };
    const badge = VERDICT_BADGE[a.verdict]
      ? `<span class="inst-status ${VERDICT_BADGE[a.verdict][1]}">${
           VERDICT_BADGE[a.verdict][0]}</span>`
      : "";
    const range = a.stem === "drums"                 // unpitched: no range
      ? (a.notes ? `${a.notes} hits` : "—")
      : a.min_pitch != null
        ? `${noteName(a.min_pitch)}–${noteName(a.max_pitch)}` : "—";
    row.innerHTML =
      `<input type="checkbox" value="${a.stem}" ${checked} ${disabled}>
       <span class="inst-name">${STEM_NAMES[a.stem] || a.stem}</span>
       <span class="inst-status ${a.status}">${a.status}</span>${badge}
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
        ? `⚠ sounds like ${heard.toLowerCase()}`
        : `sounds like: ${a.sounds_like.join(", ").toLowerCase()}`;
      box.appendChild(p);
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
  // the tempo octave is the USER's call: 152-in-eighths and
  // 76-in-sixteenths are the same audio, but the score reads differently
  if (job.bpm) {
    const t = document.createElement("div");
    t.className = "inst-tuning";
    const bpm = job.bpm;
    t.innerHTML =
      `detected tempo: <select id="instTempoScale">
         <option value="1" selected>${bpm.toFixed(0)} BPM — as detected</option>
         <option value="0.5">${(bpm / 2).toFixed(0)} BPM — half time</option>
         <option value="2">${(bpm * 2).toFixed(0)} BPM — double time</option>
       </select>`;
    box.appendChild(t);
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

  // guitar engine (task 66): auto follows the measured routing
  // (MuScriptor; GAPS for acoustic-sounding solo tracks) — the
  // dropdown lets the human overrule the router
  if (hasGuitar) {
    const eng = document.createElement("div");
    eng.className = "inst-tuning";
    eng.innerHTML =
      `guitar engine: <select id="instGuitarEngine">
         <option value="auto" selected>Auto — pick by sound</option>
         <option value="muscriptor">MuScriptor — electric &amp; mixes</option>
         <option value="gaps">GAPS — acoustic solo</option>
         <option value="bp">Basic Pitch — classic</option>
       </select>`;
    box.appendChild(eng);
  }

  box.hidden = false;
  neck.classList.remove("playing");
  showStop(false);
  setLog("Analyzed. Pick the instruments and press Transcribe.");
  goBtn.textContent = "Transcribe to tab";
  goBtn.disabled = false;

  // rhythm-precision proposal (runs AFTER the selector exists): notes
  // arriving faster than ~a third of a beat are SIXTEENTH material —
  // the default eighth grid shoves half of them onto wrong slots
  const beat = job.bpm > 0 ? 60 / job.bpm : 0;
  const fastest = Math.min(...job.analysis
    .filter((a) => a.status === "found" && a.median_ioi)
    .map((a) => a.median_ioi), Infinity);
  const precSel = $("#instPrecision");
  if (precSel && beat && fastest < 0.35 * beat) {
    precSel.value = "4";
    setLog("sixteenth-note material detected — rhythm precision set to sixteenths (change it if you disagree)");
  }
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
                     other_left: "Other · left hand",
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
  // the editor endpoints (repin/bulk/lyrics) address THIS job: a page
  // that lands on a finished job without passing through the analyze
  // screen (restored project, direct poll) had currentJobId=null and
  // every edit died with a silent 404
  currentJobId = job.id;
  activeJobId = job.id;

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

  const refLink = $("#refLink");
  refLink.href = withToken(`/api/jobs/${job.id}/reference`);
  refLink.hidden = false;
  $("#reviewBtn").hidden = false;
  loadChords(job.id);
  loadSections(job.id);
  loadLyrics(job.id);

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
  // any *_left is the bass-clef half of its instrument's grand staff
  return stem.endsWith("_left") ? stem.slice(0, -5) : stem;
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
  // drums live outside parts.json — the mass editor can't touch them
  bulk.parts = job.results.filter((r) => r.stem !== "drums")
                          .map((r) => r.stem);
  setBulkSelection(null);
  exitReview();

  const makeApi = (withPlayer) => {
    const api = new alphaTab.AlphaTabApi(atEl, {
      file: withToken(job.song),
      // per-note hit boxes are OFF by default — without them
      // noteMouseDown never fires and clicking a tab digit does nothing
      core: { includeNoteBounds: true },
      player: withPlayer ? {
        enablePlayer: true,
        soundFont: "https://cdn.jsdelivr.net/npm/@coderline/alphatab@1.4.0/dist/soundfont/sonivox.sf2",
        // alphaTab's own auto-scroll fought the page layout (it kept
        // snapping to the top) — we follow the cursor ourselves from
        // playedBeatChanged, so its scrolling is OFF entirely
        scrollMode: alphaTab.ScrollMode.Off,
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
        if (t.name.endsWith("_left")) {
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
        const ci = chordAtTicks(beat.absolutePlaybackStart);
        if (ci >= 0) highlightChord(ci);
        updateLyricLine(beat.absolutePlaybackStart);
        followCursor(beat);
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
    // drag-selecting bars on the score doubles as the MASS-EDIT range
    api.playbackRangeChanged.on((e) =>
      setBulkSelection(e ? e.playbackRange : null));
    // review-mode marks live on top of the rendered score
    api.renderFinished.on(() => { drawReviewMarks(); buildNoteIndex(); });
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
    // the tuning at the nut: which note the OPEN string is
    const open = svgEl("text", { x: 2, y: sy(row) + 3.5,
                                 class: "v-open" });
    open.textContent = NOTE_LETTERS[tuning[row] % 12];
    svg.appendChild(open);
    const dots = [];
    for (let f = 0; f <= MAX_VFRET; f++) {
      const c = svgEl("circle", { cx: fx(f), cy: sy(row), r: 7,
                                  class: "v-dot" });
      c.dataset.s = row + 1;                    // string 1..n from the top
      c._midi = tuning[row] + f;                // click = hear this note
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
    const dot = e.target.closest(".v-dot");
    if (dot && dot._midi != null) playPluck(dot._midi);
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
    r._midi = m;
    svg.appendChild(r);
    virtual.keyEls[m] = r;
  });
  whites.forEach((m, i) => {             // note letters + octave marks
    const t = svgEl("text", { x: i * kw + kw / 2, y: H - 4,
                              class: "v-key-label" });
    t.textContent = NOTE_LETTERS[m % 12];
    svg.appendChild(t);
    if (m % 12 === 0) {                  // every C carries its octave
      const o = svgEl("text", { x: i * kw + kw / 2, y: H - 16,
                                class: "v-key-octave" });
      o.textContent = "C" + (Math.floor(m / 12) - 1);
      svg.appendChild(o);
    }
  });
  whites.forEach((m, i) => {                     // blacks overlay
    if (m + 1 <= HI && isBlack(m + 1)) {
      const r = svgEl("rect", { x: (i + 0.65) * kw, y: 0,
                                width: kw * 0.7, height: H * 0.6,
                                class: "v-black" });
      r._midi = m + 1;
      svg.appendChild(r);
      virtual.keyEls[m + 1] = r;
    }
  });
  virtual.chordText = svgEl("text", { x: W - 6, y: 14, class: "v-chord" });
  svg.appendChild(virtual.chordText);
  svg.addEventListener("click", (e) => {
    const key = e.target.closest(".v-white, .v-black");
    if (key && key._midi != null) playTone(key._midi);
  });
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
  svg.addEventListener("click", (e) => {
    const pad = e.target.closest(".v-pad");
    const idx = virtual.padEls.indexOf(pad);
    if (idx >= 0) playDrum(DRUM_PADS[idx][1][0]);
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
      if (el && virtual.labelLayer) {   // a BIG letter riding the dot
        const t = svgEl("text", { x: el.getAttribute("cx"),
                                  y: parseFloat(el.getAttribute("cy")) + 3.5,
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
  // the editor moves a note between STRINGS — keys, vocals and other
  // notation-only parts have nothing to choose (their gp5 "tuning" is
  // just the pitch-encoding trick, not real strings)
  if (unified.tabByName[track.name] === false) return;
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

function toast(msg, isError = false) {
  let el = document.querySelector(".toast");
  if (!el) {
    el = document.createElement("div");
    el.className = "toast";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.classList.toggle("error", isError);
  el.classList.add("show");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("show"), 3200);
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
    // the pin is applied — keep it or put the note back
    $("#undoBtn").hidden = false;
    $("#approveBtn").hidden = false;
    closePopover();
    reloadScore(data.song);
  } catch (err) {
    closePopover();
    toast(`Note edit failed: ${err.message}`, true);
  }
}

function reloadScore(songUrl) {
  if (!unified.api) return;
  // an edit must not throw the reader away: keep the scroll position
  // and the cursor across the reload
  const keepY = window.scrollY;
  const keepTick = unified.api.tickPosition || 0;
  const once = () => {
    unified.api.renderFinished.off(once);
    window.scrollTo({ top: keepY });
    if (keepTick) unified.api.tickPosition = keepTick;
    follow.lineY = null;
  };
  unified.api.renderFinished.on(once);
  // cache-buster: the gp5 on disk changed but the URL did not
  unified.api.load(withToken(songUrl) +
    (songUrl.includes("?") ? "&" : "?") + "v=" + Date.now());
}

/* ---------- follow the playback cursor ------------------------------- */

// The HUMAN owns the scrollbar. Auto-following is OFF unless the
// user turns it on with the ⤓ button — and ANY manual scroll (wheel,
// trackpad, dragging the scrollbar itself) turns it off again. Our
// own programmatic scrolls are marked so they don't self-disable.
const follow = { lineY: null, enabled: false, ours: 0 };

function setFollow(on) {
  follow.enabled = on;
  follow.lineY = null;
  $("#followBtn")?.classList.toggle("active", on);
}

$("#followBtn")?.addEventListener("click", () =>
  setFollow(!follow.enabled));

window.addEventListener("scroll", () => {
  if (follow.ours > 0) return;        // that one was us
  if (follow.enabled) setFollow(false);
}, { passive: true });

function followCursor(beat) {
  // ride along only while actually playing: a paused user scrolling
  // around must never be yanked back
  const api = unified.api;
  if (!api || api.playerState !== alphaTab.synth.PlayerState.Playing)
    return;
  if (!follow.enabled) return;
  // page-turn behavior: while the cursor stays on the SAME staff line
  // the page does not move at all; entering a new line scrolls once.
  // (Band-keeping scrolled on every beat and fought its own smooth
  // animation — the view wobbled back and forth.)
  const bl = api.renderer?.boundsLookup || api.boundsLookup;
  const bb = bl?.findBeat ? bl.findBeat(beat) : null;
  const line = bb?.barBounds?.masterBarBounds?.visualBounds
            || bb?.barBounds?.masterBarBounds?.realBounds;
  if (!line || !line.h) return;
  if (follow.lineY === line.y) return;
  follow.lineY = line.y;
  const atAbsTop = $("#unifiedScore").getBoundingClientRect().top
                 + window.scrollY;
  follow.ours += 1;
  setTimeout(() => { follow.ours -= 1; }, 900);   // smooth scroll spans
  window.scrollTo({ top: Math.max(0, atAbsTop + line.y - 320),
                    behavior: "smooth" });
}

/* ---------- click-to-hear on the virtual instrument ------------------ */

const toneBox = { ctx: null };

function _audio() {
  if (!toneBox.ctx) toneBox.ctx = new (window.AudioContext
                                       || window.webkitAudioContext)();
  if (toneBox.ctx.state === "suspended") toneBox.ctx.resume();
  return toneBox.ctx;
}

function playTone(midi, dur = 0.6) {
  // keys mode: a small additive "piano-ish" voice
  const ctx = _audio();
  const t = ctx.currentTime;
  const f0 = 440 * Math.pow(2, (midi - 69) / 12);
  const out = ctx.createGain();
  out.gain.value = 0.28;
  out.connect(ctx.destination);
  [[1, 1, 1.6], [2, 0.5, 1.0], [3, 0.25, 0.6], [4.01, 0.1, 0.4]]
    .forEach(([mult, amp, dec]) => {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.type = "sine";
      o.frequency.value = f0 * mult;
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(amp, t + 0.008);
      g.gain.exponentialRampToValueAtTime(0.0001, t + dec);
      o.connect(g).connect(out);
      o.start(t); o.stop(t + dec + 0.05);
    });
}

function playPluck(midi, dur = 1.2) {
  // fretboard mode: Karplus-Strong — an actual plucked string
  const ctx = _audio();
  const sr = ctx.sampleRate;
  const f0 = 440 * Math.pow(2, (midi - 69) / 12);
  const period = Math.max(2, Math.round(sr / f0));
  const n = Math.floor(sr * dur);
  const buf = ctx.createBuffer(1, n, sr);
  const d = buf.getChannelData(0);
  for (let i = 0; i < period; i++) d[i] = Math.random() * 2 - 1;
  for (let i = period; i < n; i++)
    d[i] = 0.996 * 0.5 * (d[i - period] + d[i - period + 1]);
  const src = ctx.createBufferSource();
  src.buffer = buf;
  const g = ctx.createGain();
  g.gain.value = 0.5;
  src.connect(g).connect(ctx.destination);
  src.start();
}

function playDrum(gm) {
  const ctx = _audio();
  const t = ctx.currentTime;
  const out = ctx.createGain();
  out.gain.value = 0.5;
  out.connect(ctx.destination);
  if (gm === 35 || gm === 36) {                    // kick: falling sine
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.frequency.setValueAtTime(130, t);
    o.frequency.exponentialRampToValueAtTime(45, t + 0.12);
    g.gain.setValueAtTime(0.9, t);
    g.gain.exponentialRampToValueAtTime(0.001, t + 0.25);
    o.connect(g).connect(out); o.start(t); o.stop(t + 0.3);
    return;
  }
  const len = (gm === 49 || gm === 57 || gm === 51) ? 0.9 : 0.18;
  const buf = ctx.createBuffer(1, ctx.sampleRate * len, ctx.sampleRate);
  const d = buf.getChannelData(0);
  for (let i = 0; i < d.length; i++)
    d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / d.length, 2);
  const src = ctx.createBufferSource();
  src.buffer = buf;
  const f = ctx.createBiquadFilter();
  if (gm === 38 || gm === 40) { f.type = "bandpass"; f.frequency.value = 1800; }
  else if (gm >= 41 && gm <= 47) { f.type = "bandpass"; f.frequency.value = 350; }
  else { f.type = "highpass"; f.frequency.value = 6000; }   // hats/cymbals
  src.connect(f).connect(out);
  src.start(t);
}

/* ---------- note hover & click feedback ------------------------------ */

const noteFx = { index: [], hoverEl: null };

function buildNoteIndex() {
  noteFx.index = [];
  const api = unified.api;
  if (!api) return;
  const bl = api.renderer?.boundsLookup || api.boundsLookup;
  if (!bl) return;
  for (const system of bl.staffSystems || [])
    for (const mb of system.bars || [])
      for (const barBounds of mb.bars || [])
        for (const beat of barBounds.beats || [])
          for (const nb of beat.notes || []) {
            const r = nb.noteHeadBounds || nb.realBounds;
            if (r) noteFx.index.push({ r, note: nb.note });
          }
}

function noteAt(x, y, pad = 3) {
  for (const it of noteFx.index) {
    const { r } = it;
    if (x >= r.x - pad && x <= r.x + r.w + pad
        && y >= r.y - pad && y <= r.y + r.h + pad) return it;
  }
  return null;
}

function placeOver(el, r, pad) {
  // a fixed-size ring centered on the note HEAD: raw bounds can span
  // the whole stem/beam and turned the marker into an ugly blob
  const size = 15 + pad * 2;
  el.style.left = (r.x + r.w / 2 - size / 2) + "px";
  el.style.top = (r.y + r.h / 2 - size / 2) + "px";
  el.style.width = size + "px";
  el.style.height = size + "px";
}

(() => {
  const atEl = $("#unifiedScore");
  if (!atEl) return;
  atEl.addEventListener("mousemove", (e) => {
    const box = atEl.getBoundingClientRect();
    const hit = noteAt(e.clientX - box.left, e.clientY - box.top);
    if (!hit) {
      if (noteFx.hoverEl) { noteFx.hoverEl.remove(); noteFx.hoverEl = null; }
      atEl.style.cursor = "";
      return;
    }
    if (!noteFx.hoverEl) {
      noteFx.hoverEl = document.createElement("div");
      noteFx.hoverEl.className = "note-hover";
      atEl.appendChild(noteFx.hoverEl);
    }
    placeOver(noteFx.hoverEl, hit.r, 3);
    atEl.style.cursor = "pointer";
  });
  atEl.addEventListener("mouseleave", () => {
    if (noteFx.hoverEl) { noteFx.hoverEl.remove(); noteFx.hoverEl = null; }
  });
  atEl.addEventListener("mousedown", (e) => {
    const box = atEl.getBoundingClientRect();
    const hit = noteAt(e.clientX - box.left, e.clientY - box.top);
    if (!hit) return;
    const flash = document.createElement("div");
    flash.className = "note-flash";
    placeOver(flash, hit.r, 3);
    atEl.appendChild(flash);
    setTimeout(() => flash.remove(), 500);
  });
})();

/* ---------- synced lyrics (task 60) ---------------------------------- */

const lyricsUI = { segments: [], seg: -1, word: -1 };

async function loadLyrics(jobId) {
  lyricsUI.segments = [];
  lyricsUI.seg = lyricsUI.word = -1;
  try {
    const res = await apiFetch(`/api/jobs/${jobId}/lyrics`);
    if (res.ok) lyricsUI.segments = (await res.json()).segments || [];
  } catch { /* decoration only */ }
  $("#lyricsBtn").hidden = !lyricsUI.segments.length;
  $("#lyricsBar").hidden = true;
  $("#lyricsPanel").hidden = true;
  renderLyricsPanel(jobId);
}

function renderLyricsPanel(jobId) {
  const panel = $("#lyricsPanel");
  if (!panel) return;
  panel.innerHTML = "";
  lyricsUI.segments.forEach((seg, i) => {
    const row = document.createElement("div");
    row.className = "lyrics-seg" + (seg.junk ? " junk" : "")
                  + (seg.hidden ? " hidden-seg" : "");
    const eye = document.createElement("button");
    eye.textContent = seg.hidden ? "🚫" : "👁";
    eye.title = seg.hidden ? "Show this segment"
                           : "Hide this segment (junk words)";
    eye.addEventListener("click", async () => {
      try {
        const res = await apiFetch(`/api/jobs/${jobId}/lyrics`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ index: i, hidden: !seg.hidden }),
        });
        if (!res.ok) throw new Error(await errorDetail(res));
        seg.hidden = !seg.hidden;
        renderLyricsPanel(jobId);
      } catch (err) { setLog(`Lyrics toggle failed: ${err.message}`, true); }
    });
    const t = document.createElement("span");
    const m = Math.floor(seg.start / 60);
    t.className = "lyrics-time";
    t.textContent = `${m}:${(seg.start % 60).toFixed(0).padStart(2, "0")}`;
    const text = document.createElement("span");
    text.textContent = seg.words.map((w) => w.word).join(" ")
                     + (seg.junk ? "  ⚠" : "");
    text.addEventListener("click", () => {
      if (unified.api) unified.api.tickPosition = seg.words[0].qticks || 0;
    });
    row.append(eye, t, text);
    panel.appendChild(row);
  });
}

function updateLyricLine(ticks) {
  const segs = lyricsUI.segments;
  if (!segs.length) return;
  let si = -1;
  for (let i = 0; i < segs.length; i++) {
    const ws = segs[i].words;
    if (ws.length && ws[0].qticks <= ticks
        && ticks <= ws[ws.length - 1].qticks + 960 * 4) si = i;
  }
  const bar = $("#lyricsBar");
  if (si < 0 || segs[si].hidden) { bar.hidden = true; lyricsUI.seg = -1; return; }
  if (si !== lyricsUI.seg) {
    bar.innerHTML = "";
    segs[si].words.forEach((w) => {
      const span = document.createElement("span");
      span.textContent = w.word;
      span.addEventListener("click", () => {
        if (unified.api) unified.api.tickPosition = w.qticks || 0;
      });
      bar.appendChild(span);
    });
    bar.hidden = false;
    lyricsUI.seg = si;
    lyricsUI.word = -1;
  }
  let wi = -1;
  segs[si].words.forEach((w, k) => { if (w.qticks <= ticks) wi = k; });
  if (wi !== lyricsUI.word) {
    [...bar.children].forEach((el, k) =>
      el.classList.toggle("sung", k === wi));
    lyricsUI.word = wi;
  }
}

$("#lyricsBtn")?.addEventListener("click", () => {
  const p = $("#lyricsPanel");
  p.hidden = !p.hidden;
});

/* ---------- song structure (task 59) --------------------------------- */

const SECTION_HUES = { Intro: 200, Verse: 90, Chorus: 28, Bridge: 275,
                       Outro: 200 };

async function loadSections(jobId) {
  const bar = $("#sectionBar");
  if (!bar) return;
  let secs = [];
  try {
    const res = await apiFetch(`/api/jobs/${jobId}/sections`);
    if (res.ok) secs = (await res.json()).sections || [];
  } catch { /* decoration only */ }
  bar.innerHTML = "";
  bar.hidden = !secs.length;
  if (!secs.length) return;
  const total = secs[secs.length - 1].end - secs[0].start || 1;
  secs.forEach((s, i) => {
    const band = document.createElement("button");
    band.className = "section-band";
    band.textContent = s.label;
    band.title = `${s.label} — click to jump, double-click to rename`;
    band.style.flexGrow = String(Math.max(s.end - s.start, 1) / total);
    const hue = SECTION_HUES[s.label] ?? (i * 67) % 360;
    band.style.background = `hsl(${hue} 30% 26%)`;
    band.addEventListener("click", () => {
      if (unified.api) unified.api.tickPosition = s.qticks || 0;
    });
    band.addEventListener("dblclick", async () => {
      const label = prompt("Section name:", s.label);
      if (!label || label === s.label) return;
      try {
        const res = await apiFetch(`/api/jobs/${jobId}/sections`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ index: i, label }),
        });
        if (!res.ok) throw new Error(await errorDetail(res));
        const data = await res.json();
        loadSections(jobId);
        reloadScore(data.song);        // the gp5 markers follow
      } catch (err) {
        setLog(`Rename failed: ${err.message}`, true);
      }
    });
    bar.appendChild(band);
  });
}

/* ---------- chord line (task 58) ------------------------------------- */

const chordLine = { spans: [], current: -1 };

async function loadChords(jobId) {
  const bar = $("#chordBar");
  chordLine.spans = [];
  chordLine.current = -1;
  try {
    const res = await apiFetch(`/api/jobs/${jobId}/chords`);
    if (res.ok) chordLine.spans = (await res.json()).chords || [];
  } catch { /* decoration only */ }
  if (!bar) return;
  bar.innerHTML = "";
  bar.hidden = !chordLine.spans.length;
  chordLine.spans.forEach((c, i) => {
    const chip = document.createElement("button");
    chip.className = "chord-chip";
    chip.textContent = c.name;
    chip.title = "Jump here · right-click for the diagram";
    chip.addEventListener("click", () => {
      if (unified.api) unified.api.tickPosition = c.qticks;
      highlightChord(i);
      showChordOnVirtual(c);
    });
    chip.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      showChordDiagram(c, chip);
    });
    bar.appendChild(chip);
  });
}

function highlightChord(idx) {
  if (idx === chordLine.current) return;
  const bar = $("#chordBar");
  if (!bar) return;
  bar.querySelectorAll(".chord-chip").forEach((el, i) =>
    el.classList.toggle("active", i === idx));
  chordLine.current = idx;
  const el = bar.children[idx];
  // scroll the STRIP only — scrollIntoView also scrolled the PAGE
  // vertically toward the bar and kept yanking the reader's position
  if (el) bar.scrollTo({
    left: el.offsetLeft - bar.clientWidth / 2 + el.offsetWidth / 2,
    behavior: "smooth",
  });
}

function chordAtTicks(ticks) {
  const spans = chordLine.spans;
  for (let i = spans.length - 1; i >= 0; i--)
    if (spans[i].qticks <= ticks + 1) return i;
  return -1;
}

function showChordDiagram(chord, anchor) {
  closePopover();
  const pop = document.createElement("div");
  pop.className = "note-popover chord-popover";
  const title = document.createElement("h4");
  title.textContent = chord.name;
  pop.appendChild(title);
  if (chord.frets && chord.frets.length) {
    const canvas = document.createElement("canvas");
    canvas.width = 150; canvas.height = 170;
    drawChordGrid(canvas, chord.frets);
    pop.appendChild(canvas);
  } else {
    const p = document.createElement("p");
    p.textContent = "no fretted shape";
    pop.appendChild(p);
  }
  const x = document.createElement("button");
  x.className = "popover-x";
  x.textContent = "✕";
  x.addEventListener("click", closePopover);
  pop.appendChild(x);
  const r = anchor.getBoundingClientRect();
  pop.style.left = Math.min(r.left, window.innerWidth - 190) + "px";
  pop.style.top = (r.bottom + 8) + "px";
  document.body.appendChild(pop);
}

function showChordOnVirtual(c) {
  /* light the chord on the ACTIVE virtual instrument: the tab's own
     shape on the fretboard, the voicing's keys on the keyboard. The
     lit elements join virtual.lit, so the next playback highlight
     clears them naturally. */
  if (!virtual.mode) return;
  virtualLiveHighlight([]);              // clear dots + labels
  const lit = [];
  if (virtual.mode === "frets" && c.frets) {
    const n = virtual.tuning.length;
    let pi = 0;
    c.frets.forEach((f, idx) => {        // idx 0 = the LOWEST string
      if (f < 0 || idx >= n) return;
      const row = n - 1 - idx;
      const el = virtual.fretEls[row]?.[Math.min(f, MAX_VFRET)];
      const pitch = (c.pitches || [])[pi++];
      if (!el) return;
      el.classList.add("live");
      lit.push(el);
      if (virtual.labelLayer && pitch != null) {
        const t = svgEl("text", { x: el.getAttribute("cx"),
                                  y: parseFloat(el.getAttribute("cy")) + 3.5,
                                  class: "v-note-name" });
        t.textContent = NOTE_LETTERS[pitch % 12];
        virtual.labelLayer.appendChild(t);
      }
    });
  } else if (virtual.mode === "keys" && (c.pitches || []).length) {
    for (const p of c.pitches) {
      const el = virtual.keyEls[p];
      if (el) { el.classList.add("live"); lit.push(el); }
    }
  }
  virtual.lit.push(...lit);
  if (virtual.chordText) virtual.chordText.textContent = c.name;
}

function drawChordGrid(canvas, frets) {
  // frets: low string first, -1 = muted, 0 = open
  const ctx = canvas.getContext("2d");
  const n = frets.length;
  const left = 22, top = 34, w = 106, h = 120, rows = 5;
  const pressed = frets.filter((f) => f > 0);
  const base = pressed.length ? Math.min(...pressed) : 1;
  const first = Math.max(1, Math.min(base, Math.max(...pressed, 1) - rows + 1));
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = "#4a3c33"; ctx.fillStyle = "#4a3c33";
  ctx.lineWidth = first === 1 ? 4 : 1;
  ctx.strokeRect(left, top, w, 0.5);          // nut / first line
  ctx.lineWidth = 1;
  for (let r = 0; r <= rows; r++) {
    const y = top + (h / rows) * r;
    ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(left + w, y); ctx.stroke();
  }
  for (let s = 0; s < n; s++) {
    const x = left + (w / (n - 1)) * s;
    ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, top + h); ctx.stroke();
  }
  if (first > 1) {
    ctx.font = "11px sans-serif";
    ctx.fillText(String(first) + "fr", left + w + 4, top + h / rows / 2 + 4);
  }
  ctx.font = "13px sans-serif";
  frets.forEach((f, s) => {
    const x = left + (w / (n - 1)) * s;
    if (f < 0) { ctx.fillText("✕", x - 4, top - 8); return; }
    if (f === 0) {
      ctx.beginPath(); ctx.arc(x, top - 12, 4, 0, 7); ctx.stroke();
      return;
    }
    const y = top + (h / rows) * (f - first + 0.5);
    ctx.beginPath(); ctx.arc(x, y, 6, 0, 7); ctx.fill();
  });
}

/* ---------- mass editor (task 55): drag-select bars -> operate ------- */

const bulk = { range: null, parts: [] };

function activePart() {
  return (virtual.track && virtual.track.name) || bulk.parts[0] || null;
}

function setBulkSelection(range) {
  bulk.range = range || null;
  const bar = $("#bulkBar");
  if (!bar) return;
  if (!bulk.range) { bar.hidden = true; return; }
  const part = activePart();
  if (!part) { bar.hidden = true; return; }
  $("#bulkInfo").textContent = `${STEM_NAMES[part] || part}: selection`;
  const target = $("#bulkTarget");
  target.innerHTML = "";
  for (const p of bulk.parts) {
    if (p === part) continue;
    const o = document.createElement("option");
    o.value = p;
    o.textContent = STEM_NAMES[p] || p;
    target.appendChild(o);
  }
  target.hidden = !target.options.length;
  bar.querySelector('[data-op="reassign"]').hidden = !target.options.length;
  bar.hidden = false;
}

async function runBulk(op) {
  if (!bulk.range) return;
  const part = activePart();
  if (!part) return;
  if (op === "delete"
      && !confirm("Delete every note in the selected range?")) return;
  try {
    const res = await apiFetch(`/api/jobs/${currentJobId}/bulk_edit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        part, op,
        from_qticks: Math.round(bulk.range.startTick),
        // endTick points at the first tick AFTER the selection
        to_qticks: Math.max(0, Math.round(bulk.range.endTick) - 1),
        target: $("#bulkTarget")?.value || null,
      }),
    });
    if (!res.ok) throw new Error(await errorDetail(res));
    const data = await res.json();
    setLog(`${op}: ${data.count} note(s) affected`);
    exitReview();                      // positions changed — marks stale
    reloadScore(data.song);
  } catch (err) {
    setLog(`Mass edit failed: ${err.message}`, true);
  }
}

document.querySelectorAll("#bulkBar [data-op]").forEach((b) =>
  b.addEventListener("click", () => runBulk(b.dataset.op)));
$("#bulkClear")?.addEventListener("click", () => {
  if (unified.api) unified.api.playbackRange = null;
  setBulkSelection(null);
});

/* ---------- review mode (task 55): walk the disputed notes ----------- */

const review = { on: false, part: null, notes: [], idx: 0 };
const REVIEW_CONF = 0.5;

async function enterReview() {
  const part = activePart();
  if (!part) return;
  try {
    const res = await apiFetch(`/api/jobs/${currentJobId}/notes/${part}`);
    if (!res.ok) throw new Error(await errorDetail(res));
    const data = await res.json();
    review.notes = data.notes
      .filter((n) => !n.dead && n.conf < REVIEW_CONF)
      .sort((a, b) => a.qticks - b.qticks);
  } catch (err) {
    setLog(`Review failed: ${err.message}`, true);
    return;
  }
  if (!review.notes.length) {
    setLog(`Review: no disputed notes in ${STEM_NAMES[part] || part} — ` +
           `everything above ${REVIEW_CONF} confidence`);
    return;
  }
  review.on = true;
  review.part = part;
  $("#reviewBar").hidden = false;
  drawReviewMarks();
  gotoReview(0);
}

function exitReview() {
  review.on = false;
  review.notes = [];
  const bar = $("#reviewBar");
  if (bar) bar.hidden = true;
  drawReviewMarks();
}

function gotoReview(i) {
  if (!review.notes.length) return;
  review.idx = ((i % review.notes.length) + review.notes.length)
               % review.notes.length;
  const n = review.notes[review.idx];
  $("#reviewInfo").textContent =
    `${review.idx + 1}/${review.notes.length} · confidence ${n.conf.toFixed(2)}`
    + " — click the glowing note to fix it";
  if (unified.api) unified.api.tickPosition = n.qticks;
  // the CURRENT disputed note stands out and the view rides to it
  const marks = [...$("#unifiedScore").querySelectorAll(".review-mark")];
  marks.forEach((m) =>
    m.classList.toggle("current", +m.dataset.ri === review.idx));
  const cur = marks.find((m) => +m.dataset.ri === review.idx);
  if (cur) cur.scrollIntoView({ block: "center", behavior: "smooth" });
}

function drawReviewMarks() {
  const atEl = $("#unifiedScore");
  if (!atEl) return;
  atEl.querySelectorAll(".review-mark").forEach((m) => m.remove());
  if (!review.on || !unified.api) return;
  const bl = unified.api.renderer?.boundsLookup || unified.api.boundsLookup;
  if (!bl) return;
  // one grid slot of tolerance: the collision shift can move a beat
  const slot = 960 / 2;
  const disputed = review.notes;
  const disputedIndex = (ticks, pitch) => disputed.findIndex(
    (n) => n.pitch === pitch && Math.abs(n.qticks - ticks) <= slot);
  for (const system of bl.staffSystems || []) {
    for (const mb of system.bars || []) {
      for (const barBounds of mb.bars || []) {
        if (barBounds.bar?.staff?.track?.name !== review.part) continue;
        for (const beat of barBounds.beats || []) {
          const ticks = beat.beat?.absolutePlaybackStart;
          for (const nb of beat.notes || []) {
            const pitch = nb.note?.realValue;
            if (ticks == null || pitch == null) continue;
            const ri = disputedIndex(ticks, pitch);
            if (ri < 0) continue;
            const r = nb.noteHeadBounds || nb.realBounds;
            if (!r) continue;
            const mark = document.createElement("div");
            mark.className = "review-mark";
            mark.dataset.ri = String(ri);
            mark.style.left = (r.x - 3) + "px";
            mark.style.top = (r.y - 3) + "px";
            mark.style.width = (r.w + 6) + "px";
            mark.style.height = (r.h + 6) + "px";
            atEl.appendChild(mark);
          }
        }
      }
    }
  }
  // a re-render must not lose the "you are here" emphasis
  atEl.querySelectorAll(".review-mark").forEach((m) =>
    m.classList.toggle("current", +m.dataset.ri === review.idx));
}

$("#reviewBtn")?.addEventListener("click", () =>
  review.on ? exitReview() : enterReview());
$("#reviewClose")?.addEventListener("click", exitReview);
$("#reviewPrev")?.addEventListener("click", () => gotoReview(review.idx - 1));
$("#reviewNext")?.addEventListener("click", () => gotoReview(review.idx + 1));

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
