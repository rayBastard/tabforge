# Changelog

## v0.6.0 — 2026-08-26

The accuracy war concluded and the guitarist's convenience layer.
Golden-corpus strict F1, first honest baseline -> now: guitar
0.24 -> 0.41, bass 0.34 -> 0.63, keys (broken) -> 0.58, drums
0.38 -> 0.55, vocals 0.13 -> 0.15 + honest dead-note crosses.

### Accuracy (tasks 52-57, docs/eval.md has every measurement)
- Honest evaluation ruler: per-instrument alignment (time x octave),
  pitch-only column, per-file offset discovery in Suno exports.
- Monophonic bass/vocals path (pyin + onset segmentation) with a
  density chooser falling back to Basic Pitch on dirty stems;
  recitative vocals become dead-note crosses instead of fake pitches.
- MT3 instrument-presence arbiter on the analyze cards
  (found / absent / uncertain) with content-match guards; optional
  install at ~/mt3.
- Note-source routing: keys from MT3, guitar+bass from MuScriptor
  (optional non-commercial install), drums and vocals stay native.
- The pre-export snap is gone (the gp5 writer slots notes itself):
  keys 0.27 -> 0.44, Loken bass 0.41 -> 0.61 on golden.
- Phantom drums can no longer drive the tempo (envelope crest gate);
  keys-led drumless tracks auto-suggest half time (152 -> 81 BPM on
  the piano golden, sheet says 73-82).
- Softer guitar Basic Pitch preset: +0.10 F1 on the metal golden.

### Convenience (tasks 55, 58-60)
- Mass editor: drag-select bars -> octave shift / delete / move to
  another instrument / collapse octave doubles; pins and legato
  survive.
- Review mode: per-note confidence, disputed notes highlighted and
  steppable.
- Reference export: corrected notes leave as per-instrument MIDI
  named like the golden corpus — your edits become ground truth.
- Chord line: names (power chords and slash basses included) above
  the score, synced to the cursor, fret diagrams from the actual tab
  shapes, labels in the gp5.
- Song sections: chroma novelty + chord-loop-break voting, colored
  timeline, click to jump, rename inline (gp5 markers follow).
- Synced lyrics (tabforge[lyrics], faster-whisper): running
  word-highlighted line, junk-word marking with one-click hide,
  .lrc export and the gp5 lyrics channel.
- MT3 arbiter checkbox, chords/sections and lyrics checkboxes with a
  language override on the start screen.

## v0.5.0 — 2026-08-25

Post-feedback polish (tasks 33-38 + the rhythm war): beat repair and
smoothing, rhythm precision selector, fretboard editor with
alternatives and approve/revert, 24-fret neck with note names and
chord labels, grand staff for keys, 88-key piano, project save/load
(.tabforge), spacebar transport, tab-only guitar view.

## v0.4.0 — 2026-08-25

The drum track (9-voice spectral classifier), desktop app rebuild,
tick-based export, instrument profiles, two-step analyze/transcribe
flow, the project screen and the unified multi-track player, the
note editor.

## v0.3.0 — 2026-08-25

Phases 5 and 6: the server grew up and moved out, and the app fits in
a pocket.

### Server, ready for the network
- Job lifecycle: finished jobs expire (TTL) with background cleanup,
  the store is capped with oldest-finished eviction and an honest 429.
- Input limits and validation: streaming upload size check, duration
  probe before the pipeline, real-audio validation, filename traversal
  killed (only the extension survives), readable 4xx errors in the UI.
- Optional API token (TABFORGE_TOKEN) via header or ?token= for
  downloads; the UI asks once. Configurable workers (TABFORGE_WORKERS).
- Non-wav input is re-encoded to wav before demucs — real-world mp3s
  with malformed frames no longer crash the strict decoders.

### Deployment
- Dockerfile (python 3.11-slim, ffmpeg, non-root, uvicorn) and compose
  with a model-cache volume; all knobs overridable from the host env.
  Measured: ~1.5 min for a 3-minute track in a 4-CPU container, cold
  start costs nothing extra on a fast connection.
- Cloudflare quick-tunnel service under the `tunnel` compose profile:
  free public URL, no account or public IP; token required.

### Mobile (PWA)
- manifest (standalone, dark walnut theme, generated lamp icons) and a
  version-keyed service worker: instant shell start from cache, /api/*
  always live, old shells purged on deploy.
- Mobile UI pass: 44px+ touch targets, tap-to-pick on the drop zone,
  the ASCII tab scrolls inside its container.

### Fixes & maintenance
- Frontend poll retries with backoff instead of dying on one hiccup;
  key detection failures degrade instead of killing the job; the tempo
  source falls back to the mix when the drums stem is silent.
- Review issues #1–#4 closed (subdivision & time signature wired into
  gp5, single audio decode + pruned tempo hypotheses, lazy alphaTab
  player); #5/#6 deliberately deferred; #7/#8 track the remaining
  hosting and offline tails.

## v0.2.0 — 2026-08-25

The first release where the whole path — audio in, playable tablature
out — works end to end and is verified by tests at every joint.

### Transcription & pipeline
- Tuned Basic Pitch thresholds for Suno-style guitar (fewer ghost
  fragments, chords hold together); ghost-overtone cleanup via minimum
  note length and a polyphony cap.
- Stable tempo: detected once per track from the drums stem (with an
  RMS audibility check falling back to the full mix), tempo-multiple
  disambiguation via weighted candidate families, and a sanity guard
  (fewer than 8 beats or BPM outside 40–260 → honest 120 BPM fallback
  flagged "tempo: estimated poorly").
- Key detection (Krumhansl-Schmuckler over chroma_cqt); failures
  degrade to "unknown key" instead of killing the job.
- Optional lead/rhythm guitar split (`--split-guitars` / UI checkbox):
  chords and their neighborhood go to rhythm, high single-note runs to
  lead; each part becomes its own track and card.
- Pitch-bend trajectories are kept per note and classified into
  slide / bend / vibrato; legato pairs (hammer-on / pull-off) are
  detected and rewarded in the fingering when they fit on one string.

### Fingering core
- Unplayable leading events (transcription noise) no longer wipe out
  the whole tab.
- Legato-aware transition costs; all behavior remains tunable via
  `TabConfig`.

### Export
- gp5: correct Beat/Voice structure (alphaTab-compatible), positional
  measure assembly with rest filling, measures anchored at the first
  detected beat, key signature in every measure header, time signature
  (n/4) and grid subdivision (including triplets) written through,
  articulation effects (hammer, slides, bends in quarter-tone units,
  vibrato).
- ASCII tab: articulations drawn as `5h7`/`7p5`, `/` and `\`, `~`.
- MusicXML: tempo and key signature.
- `scripts/check_gp5.py`: round-trip verification of pitches, note
  positions, per-measure key signatures, and effects.

### App
- Web UI: per-part Play button with a lazily loaded alphaTab synth
  (soundfont fetched on first click), job polling with retry/backoff,
  warnings surfaced next to BPM/key.
- Desktop: PyInstaller build → `dist/TabForge.app`; demucs runs
  strictly in a subprocess (failures surface as job errors instead of
  hanging jobs), models download to `~/.cache` on first run.

### Reliability & testing
- 93 tests (was 18): unit, gp5 round-trip, and an end-to-end synthetic
  fixture corpus (short clip, leading silence, drumless track,
  ground-truth scale, silence).
- A multi-agent code review produced 17 findings: all fixed or filed
  as issues (#1–#4 fixed, #5 deferred).

### Project
- Everything translated to English; Python 3.11 requirement documented
  (basic-pitch has no macOS support on 3.12+).

## v0.1.0

Initial skeleton: fingering core (Viterbi over hand positions),
pipeline, FastAPI server, pywebview desktop shell, frontend, CI.
