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

import numpy as np  # noqa: E402
from scipy.io import wavfile  # noqa: E402
from scipy.signal import resample_poly  # noqa: E402

from madmom.audio.signal import Signal  # noqa: E402
from madmom.features.downbeats import (  # noqa: E402
    DBNDownBeatTrackingProcessor, RNNDownBeatProcessor)

# madmom's own loader handles only what its net wants (44.1k mono) and
# shells out to ffmpeg for everything else — this venv has no ffmpeg,
# so decode + downmix + resample here with scipy instead
sr, data = wavfile.read(str(AUDIO))
orig = data.dtype
data = data.astype(np.float32)
if np.issubdtype(orig, np.integer):
    data /= np.iinfo(orig).max
if data.ndim > 1:
    data = data.mean(axis=1)
if sr != 44100:
    from fractions import Fraction
    fr = Fraction(44100, int(sr)).limit_denominator(1000)
    data = resample_poly(data, fr.numerator, fr.denominator)
    sr = 44100
sig = Signal(data.astype(np.float32), sample_rate=sr, num_channels=1)

act = RNNDownBeatProcessor()(sig)
proc = DBNDownBeatTrackingProcessor(
    beats_per_bar=[3, 4], fps=100,
    min_bpm=BPM * 0.9, max_bpm=BPM * 1.1)
res = proc(act)

# Task 74 (meter changes): ONE DBN decode commits to one bar length
# nearly globally (measured: 37 s of waltz appended to a 4/4 track
# stayed 4/4 until the final bar). So the meter is ALSO voted per
# window: the activation is the expensive part and is reused; each
# 20 s window (hop 10 s) gets its own decode and its own bar-length
# vote. The app segments these votes into meter runs.
FPS = 100
win, hop = 20 * FPS, 5 * FPS
windows = []
pos = 0
while pos < len(act):
    chunk = act[pos:pos + win]
    if len(chunk) < 8 * FPS:
        break
    try:
        wres = proc(chunk)
        # one decode = one bar length; the max position IS the vote
        vote = max((int(b) for _t, b in wres), default=0)
    except Exception:  # noqa: BLE001 — a bad window must not kill the run
        vote = 0
    windows.append([pos / FPS, min(len(act), pos + win) / FPS, vote])
    pos += hop

OUT.write_text(json.dumps(
    {"rows": [[float(a), int(b)] for a, b in res],
     "windows": windows}))
print(f"ok {OUT}", flush=True)
