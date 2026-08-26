"""Standalone MuScriptor runner — executed INSIDE its own venv, never
imported by tabforge (its dependency pins would downgrade torchaudio
under demucs).

    <dir>/venv/bin/python _muscriptor_run.py <mix> <out.mid> [variant]

Install recipe (weights are CC BY-NC 4.0, gated on Hugging Face — the
user must accept the license and hold a token in the standard HF
cache; code is MIT):

    python3.11 -m venv ~/muscriptor/venv
    ~/muscriptor/venv/bin/pip install muscriptor
"""
import sys
from pathlib import Path

# python puts the script's own directory first on sys.path — where
# tabforge's muscriptor.py WRAPPER lives, shadowing the real package
_here = Path(__file__).resolve().parent
sys.path = [p for p in sys.path if Path(p or ".").resolve() != _here]

MIX = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
VARIANT = sys.argv[3] if len(sys.argv) > 3 else "small"

from muscriptor import TranscriptionModel  # noqa: E402

model = TranscriptionModel.load_model(VARIANT)
OUT.write_bytes(model.transcribe_to_midi(str(MIX)))
print(f"ok {OUT}", flush=True)
