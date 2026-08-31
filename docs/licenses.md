# License audit (block-70 tails, 2026-08-31)

The rule the architecture already enforces: **nothing non-commercial
ships in the bundle.** Non-commercial models live in their own venvs
outside the app and are called as subprocesses; the app degrades
gracefully without them.

## Green — bundled in TabForge.app

| Component | License | Note |
|---|---|---|
| demucs (code + htdemucs_6s weights) | MIT | |
| basic-pitch | Apache-2.0 | pip metadata says UNKNOWN; the Spotify repo carries Apache-2.0 |
| librosa | ISC | |
| faster-whisper + Whisper weights | MIT | weights lazy-download |
| panns-inference (code) | MIT | Cnn14 checkpoint lazy-downloads at first use — **verify the Zenodo record's license before public release** |
| GAPS / hf-midi-transcription (code + weights) | MIT | measured task 64 |
| piano-transcription-inference | UNKNOWN in pip | **verify upstream before public release** |
| pretty_midi, mido, soundfile, FastAPI, uvicorn, pywebview, numpy, scipy, music21, torch, onnxruntime | MIT / BSD / ISC / Apache | standard |
| **PyGuitarPro** | **LGPL-3.0** | pure-python, unmodified, source public — distribution with attribution is fine, but flag it in release notes before "показать людям" |

## Yellow — NEVER bundled, external installs (subprocess pattern)

| Component | License | Isolation |
|---|---|---|
| MuScriptor weights | CC BY-NC 4.0, HF-gated | `~/muscriptor/venv`; user accepts the license on HF; NC applies to output use |
| madmom RNN downbeat models | CC BY-NC-SA 3.0 | `~/madmom/venv` (v0.7.16); spec-excluded from the bundle |
| YourMT3+ (~/mt3) | **verify repo license before public release** | external dir, never bundled |
| BS-Roformer-SW weights (roformer HQ mode) | **verify before enabling by default** | optional `--separator`, weights lazy |

## Build/dev tooling

pyinstaller (+hooks-contrib) is GPLv2 **with the bootloader
exception** — a build tool explicitly allowed to package apps of any
license; nothing of it is a runtime dependency. sphn arrived as a
transitive dep with UNKNOWN pip metadata — **verify upstream before
public release** (tracked in the audit script's allowlist).

## Dev-only (never in the product)

BeatNet (CC BY-NC-SA) + its pyaudio stub — eval-stand only,
spec-excluded. pip-licenses — audit tooling.

## How to re-run

    scripts/license_audit.sh

prints the full pip-licenses table and fails on GPL (non-LGPL) or
new UNKNOWN entries beyond the two known ones above. Run it before
any public release; wire it into CI when CI exists.
