# Changelog

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
