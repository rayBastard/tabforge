"""Standalone madmom runner — executed inside ITS OWN venv (the RNN
downbeat models are CC BY-NC-SA, so madmom is never bundled with the
app; same pattern as the MT3 and MuScriptor runners).

    <venv>/bin/python _madmom_run.py <audio.wav> <out.json> <bpm>

Writes [[time, bar_position], ...] as JSON. Install recipe:

    python3.11 -m venv ~/madmom/venv
    ~/madmom/venv/bin/pip install "cython<3" numpy scipy
    ~/madmom/venv/bin/pip install "git+https://github.com/CPJKU/madmom.git"
"""
import json
import sys
from pathlib import Path

# the script's own directory shadows nothing here, but keep the MT3
# runner's hygiene anyway
_here = Path(__file__).resolve().parent
sys.path = [p for p in sys.path if Path(p or ".").resolve() != _here]

AUDIO = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
BPM = float(sys.argv[3])

from madmom.features.downbeats import (  # noqa: E402
    DBNDownBeatTrackingProcessor, RNNDownBeatProcessor)

act = RNNDownBeatProcessor()(str(AUDIO))
res = DBNDownBeatTrackingProcessor(
    beats_per_bar=[3, 4], fps=100,
    min_bpm=BPM * 0.9, max_bpm=BPM * 1.1)(act)
OUT.write_text(json.dumps([[float(a), int(b)] for a, b in res]))
print(f"ok {OUT}", flush=True)
