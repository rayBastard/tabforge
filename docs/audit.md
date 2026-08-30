# Code & dependency audit (task 69, 2026-08-30)

6,797 lines / 33 modules at audit time (v0.7.4). Every module read,
call edges traced from the four production entry points (cli,
desktop, server routes, pipeline API). Companion: docs/perf.md
(task 68) supplied the hot-path ground truth.

## Verdict in one line

The codebase is lean: the measurement-first culture meant losing
mechanics mostly never merged. Five of the six graveyard suspects do
not exist in code; four small true-dead pieces were deleted (below),
three one-domain losers carry "kept:" markers, and the dependency
list holds no dead weight beyond the removed `crepe` extra.

## Graveyard sweep (eval.md burials vs the code)

| mechanic | in code? | action |
|---|---|---|
| octave-dedup variants (lower-wins / energy-wins) | never merged — only the manual `dedup_octaves` bulk op (HOT) | none |
| symmetric leak filter | never merged (single asymmetric impl) | none |
| spectral octave choice / f0-half check | never merged | none |
| MuScriptor conditioning | orphaned plumbing in `_muscriptor_run.py` | **deleted** |
| naive per-chord section voting | never merged (loop-period vote only) | none |
| GAPS guitar-fl checkpoint | docs only | none |

## Deleted (each its own commit)

1. MuScriptor `instruments=` conditioning plumbing — buried in
   eval.md "MUSCRIPTOR-MEDIUM (task 63)": max +0.03, piano −0.09.
2. `mono._f0_torchcrepe` + the `backend` parameter + the `crepe`
   extra — unreachable: no preset or caller ever selected it; pyin
   won the mono path on the stand (task 53).
3. `quantize.duration_symbol` + `split_measures` + `DURATION_VALUES`
   — pre-writer relics; the gp5 writer owns durations since task 56,
   nothing referenced them outside one test.
4. `PipelineOptions.split_guitars` + its CLI/server/frontend setters
   — written, never read: the lead/rhythm split has been
   unconditional since the auto-detection landed.

Also fixed en route: `cli.py --quantize` default was still 0.9 (the
pre-task-56 snap that measurably destroys timing); now 0.0 like the
product.

## Kept with markers (lost in one domain, may win in another)

- `audio/lowregister.py` — octave double-pass; lost to the mono path,
  unreachable today (no flag exposure), may win on drop-tuned guitar.
- `transcribe.separate_stems_roformer` — cleaner stems, 30× slower;
  live behind `--separator/TABFORGE_SEPARATOR/HQ checkbox`.
- MT3-as-note-source (pipeline keys branch) — beaten by
  muscriptor-medium but the winner for every install without the
  gated MuScriptor weights.

## Classification highlights (full trace in the audit sweep)

- Everything in `core/`, `export/writers.py`, `server/app.py`,
  `audio/{drums,keydetect,mono,sections,tagging,validate,midi_in,
  lyrics}` is HOT.
- FLAG-gated: arbiter (+`_mt3_run`) on an MT3 install; muscriptor
  (+runner) on a MuScriptor install; gaps on `guitar_engine`;
  roformer on `separator`; `_analyze_solo` on `solo`;
  `_analyze_mix_only` on CLI `--stems mix`.
- TESTS/SCRIPTS-only: `export/gp5_read.py` (the round-trip reader —
  test infrastructure, staying).
- Vestigial but harmless: `subdivision` still flows into Grid/opts
  though the adaptive writer ignores it (Grid still serves chords and
  beat windows); cleaning it out is not worth the churn.

## Dependencies (pyproject)

Every remaining extra is production-imported on its path: `ml`
(basic-pitch, demucs, librosa, panns), `export` (PyGuitarPro,
pretty_midi, music21), `server`, `desktop`, `lyrics`
(faster-whisper), `roformer`, `eval` (mir_eval, dev). `crepe` —
removed with its code. The `numpy<2.0` and `setuptools<81` pins and
the resampy 0.4.2 note are all still load-bearing (basic-pitch and
the torch stack; documented in pyproject comments).

## Basic Pitch: needed or not (measured answer, no new runs needed)

As a routed transcriber with all installs present it wins **no row**
(muscriptor-medium and GAPS beat it everywhere measured). But it is
NOT removable from the hot pipeline:

- it is the only transcriber for the `other` stem and the whole-mix
  fallback — and the no-install floor for guitar/bass/keys
  (MuScriptor weights are gated + non-commercial, MT3 is a manual
  install; the out-of-box app has neither);
- analyze uses it to build every instrument card (0.4–1.7 s/stem);
- the vocals density chooser uses it (and it WINS dirty vocal stems).

Dropping it would also buy nothing in dependencies: torch (demucs,
panns) keeps the heavy stack regardless, and onnxruntime is pulled by
faster-whisper, not BP. Verdict: **stays in the hot pipeline**.

## Bundle (903 MB unpacked / 296 MB zip)

| item | MB | why | lazy-loadable? |
|---|---:|---|---|
| torch (Frameworks+Resources) | 409 | demucs, panns, torchlibrosa | no — it is code |
| llvmlite | 123 | numba ← librosa (pyin) | no |
| onnxruntime | 71 | faster-whisper VAD | only by cutting lyrics from the bundle |
| av (PyAV/ffmpeg) | 43 | faster-whisper audio | same |
| scipy+numpy+sklearn | 86 | librosa stack | no |

Model weights are already lazy (PANNs → ~/panns_data, whisper → HF
cache, GAPS → HF cache; only BP's 3 MB CoreML model ships). The
bundle is torch-bound: no meaningful lazy-download exists without
cutting features. Possible cosmetic trim: torch's `_inductor`/
`testing`/`distributed` Resources (~25 MB) via spec excludes.

## Acceptance (spec item 6)

- Tests: 253 green after all deletions (3 duration_symbol tests
  removed with their function).
- Golden metrics: full eval_golden rerun after the deletions —
  every instrument mean matches the recorded scoreboard exactly
  (bass 0.63, drums 0.55, guitar 0.41, piano 0.65, vocals 0.16):
  the deleted code was dead, the numbers did not move.
- App start (launch → first HTTP response): 0.46 s before and after
  (the deletions are pure-Python lines).
- Bundle: 296 MB zip before → after: unchanged (no library left the
  bundle; the `crepe` extra was never bundled).
