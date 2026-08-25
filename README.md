# TabForge

Audio (including tracks from Suno) → tablature and sheet music.
One codebase — three platforms: laptop today, then browser, then mobile.

## How it works

The app is a local server (FastAPI) + a web UI.
On a laptop the UI opens in a native window (pywebview), in a browser —
at the server's address, on a phone — as a PWA. The logic is never rewritten.

```
┌─────────────────────────── frontend/ ────────────────────────────┐
│  the same HTML/JS: window on a laptop, browser tab, phone screen  │
└──────────────────────────────┬───────────────────────────────────┘
                        HTTP (localhost or network)
┌──────────────────────────────┴───────────────────────────────────┐
│ server/   FastAPI: jobs, progress, file delivery                  │
│ pipeline  Demucs → Basic Pitch → librosa → fretboard → export     │
│ core/     fingering (Viterbi over hand positions) — core, 0 deps  │
└──────────────────────────────────────────────────────────────────┘
```

## Roadmap

### Phase 0 — repository skeleton ✅ (done)
- [x] `src/`, `frontend/`, `tests/` structure
- [x] `pyproject.toml` with extras: the core installs without ML dependencies
- [x] CI on GitHub Actions (Python 3.10–3.12)
- [x] MIT license, .gitignore

### Phase 1 — core ✅ (done, 18 tests)
- [x] `core/fretboard.py`: note → string+fret. Viterbi with a
      "fingering + hand position" state. Verified: the C major scale lands
      in first position, Em/G/C/D chords in open shapes,
      solos in a compact box
- [x] `core/quantize.py`: snapping to the beat grid, note durations
- [x] tunings: standard, drop D, E♭, DADGAD, open G, 4/5-string bass, ukulele

### Phase 2 — audio pipeline 🔧 (code written, needs a run)
- [x] `audio/transcribe.py`: Demucs (stems) + Basic Pitch (notes) + librosa (tempo)
- [x] `pipeline.py`: a single entry point for CLI/server/desktop
- [ ] **run it on a real Suno track** ← you are here
- [ ] tune Basic Pitch thresholds for the character of Suno tracks
- [ ] ghost-overtone cleanup: currently a heuristic, could be smarter

### Phase 3 — export 🔧 (code written, needs verification)
- [x] MIDI, ASCII tab
- [x] .gp5 (PyGuitarPro) — **verify it opens in Guitar Pro/TuxGuitar**,
      the library is picky about the Beat/Voice structure
- [x] MusicXML (music21) for MuseScore
- [ ] key signatures (needs key detection)
- [ ] techniques: hammer-on/pull-off, slides, bends from pitch-bend data

### Phase 4 — laptop app 🔧 (code written)
- [x] server: POST /api/jobs, progress, file download
- [x] UI: upload, "fretboard-style" progress, ASCII tab,
      notes+tabs rendering via alphaTab from .gp5
- [x] `desktop.py`: native window (pywebview)
- [ ] verify end-to-end
- [x] app bundle build: `pyinstaller TabForge.spec` → `dist/TabForge.app`
      (see the Building section; Demucs models download on first run
      into `~/.cache`)

### Phase 5 — browser
- [ ] deploy the server (needs GPU hosting or patience on CPU)
- [ ] a job queue instead of ThreadPool (e.g. arq/Celery)
- [ ] limits on file size and processing time

### Phase 6 — mobile
- [ ] PWA manifest + offline shell (the UI is already responsive)
- [ ] the "everything on the server, the phone is just a client" option works right away

## Installation (development)

```bash
git clone https://github.com/<your-username>/tabforge && cd tabforge
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[all]"
```

## Running

```bash
# the app (native window)
python -m tabforge.desktop

# the same, but in a browser
uvicorn tabforge.server.app:app --port 8000   # open http://localhost:8000

# console, no UI
tabforge song.mp3 --stems guitar bass --out ./result

# core tests (fast, no ML)
python -m unittest discover -s tests
```

## Building (desktop app)

```bash
pip install pyinstaller
pyinstaller TabForge.spec        # -> dist/TabForge.app (macOS)
```

Notes:

- The entry point is `scripts/desktop_launcher.py` (an entry script runs
  outside the package, so it wraps `tabforge.desktop` with an absolute
  import); `frontend/` ships inside the bundle and the server resolves it
  via `sys._MEIPASS` when frozen.
- The Demucs model weights are **not** bundled: on the first run the app
  downloads them into `~/.cache/huggingface` (~50 MB), exactly like the
  dev setup. Everything else (Basic Pitch's CoreML model, librosa/resampy
  data) is inside the bundle.
- The bundle is large (~700 MB) — that's PyTorch.
- `dist/` and `build/` are git-ignored; `TabForge.spec` is the build
  definition and lives in git.

## Publishing to GitHub

```bash
cd tabforge
git remote add origin git@github.com:<your-username>/tabforge.git
git push -u origin main
```

CI starts on its own: every push runs the core tests on three Python versions.

## Tuning the fingering

All behavior lives in `TabConfig` (`src/tabforge/core/fretboard.py`):

| Don't like | Tweak |
|---|---|
| the solo drifts high up the neck | ↑ `high_fret_penalty` |
| too many open strings | ↓ `open_string_bonus` |
| the hand jumps around the neck | ↑ `move_penalty` |
| you need wide stretches | ↑ `max_stretch`, ↓ `stretch_penalty` |

## Honest limitations

- Polyphonic guitar transcription is an unsolved problem: expect 70–90%
  on clean tone, worse with distortion. Bass transcribes almost perfectly.
- Guitar sounds an octave lower than written — if the notes seem "off",
  check this first.
- Suno tracks are generated, not played: physically unplayable voicings
  do occur. The algorithm finds the closest playable one.
- The time signature is hardcoded to 4/4; tempo changes within a track
  are averaged for now.
