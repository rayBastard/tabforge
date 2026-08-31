"""
Palm-mute detection (deferred-techniques block, 2026-08-31).

A palm-muted attack is physically distinct: the string is damped at
the bridge, so the note DECAYS fast and its spectrum loses the upper
harmonics (dull, chuggy). Both cues are read off the guitar STEM at
each transcribed note's onset:

- decay = RMS(90-180 ms) / RMS(0-60 ms)  -> muted notes fall fast
- brightness = spectral centroid of the first 120 ms over the note's
  own f0 -> muted notes sit within the first few harmonics

Speckle guard (the uniformity-via-price doctrine): palm mutes come in
RUNS (chugging), so only stretches of >= MIN_RUN consecutive muted
attacks are marked — one dull note in a ringing passage stays plain.
Calibrated on Karplus-Strong synthesis (tests) where damping is the
ground truth; the final judge is the score.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

DECAY_MAX = 0.45          # RMS(late)/RMS(early) below this = damped
BRIGHT_MAX = 16.0         # centroid / f0 below this = dull (open
                          # notes measured ~23-24, muted ~10-14)
DUR_MAX = 0.5             # a palm-muted chug is short
MIN_RUN = 3               # consecutive muted attacks to mark a run
MAX_GAP = 0.6             # a run tolerates this much silence inside


def detect_palm_mutes(notes: Sequence, wav: Path) -> int:
    """Set .palm_mute on the notes (in place); returns how many."""
    if not notes:
        return 0
    import librosa
    import numpy as np

    y, sr = librosa.load(str(wav), sr=22050, mono=True)
    if not len(y):
        return 0

    def _rms(a: "np.ndarray") -> float:
        return float(np.sqrt(np.mean(a * a))) if len(a) else 0.0

    n_fft = 1024
    order_t = sorted(n.start for n in notes)
    import bisect
    cand: list[bool] = []
    for n in notes:
        s0 = int(n.start * sr)
        # windows adapt to the gap before the NEXT attack, or a dense
        # riff's next chug lands inside the "late" window and masks
        # the decay (measured on Loken 16th chugging)
        j = bisect.bisect_right(order_t, n.start + 1e-4)
        gap = (order_t[j] - n.start) if j < len(order_t) else 1.0
        e_end = min(0.06, max(0.02, gap * 0.4))
        l0 = max(e_end + 0.01, min(0.09, gap * 0.5))
        l1 = min(0.18, gap * 0.95)
        early = y[s0:s0 + int(e_end * sr)]
        late = y[s0 + int(l0 * sr):s0 + int(l1 * sr)]
        e1, e2 = _rms(early), _rms(late)
        head = y[s0:s0 + int(0.12 * sr)]
        if e1 < 1e-4 or len(late) < 200 or len(head) < n_fft // 2 \
                or n.dead:
            cand.append(False)
            continue
        decay = e2 / e1
        spec = np.abs(np.fft.rfft(head * np.hanning(len(head))))
        freqs = np.fft.rfftfreq(len(head), 1 / sr)
        centroid = float((spec * freqs).sum() / (spec.sum() + 1e-9))
        f0 = 440.0 * 2 ** ((n.pitch - 69) / 12)
        cand.append(decay < DECAY_MAX
                    and centroid / f0 < BRIGHT_MAX
                    and n.duration < DUR_MAX)

    order = sorted(range(len(notes)), key=lambda i: notes[i].start)
    marked = 0
    run: list[int] = []

    def _flush() -> None:
        nonlocal marked
        if len(run) >= MIN_RUN:
            for i in run:
                notes[i].palm_mute = True
            marked += len(run)

    prev_t = None
    for i in order:
        if cand[i] and (prev_t is None or not run
                        or notes[i].start - prev_t <= MAX_GAP):
            run.append(i)
        elif cand[i]:
            _flush()
            run = [i]
        else:
            _flush()
            run = []
        prev_t = notes[i].start
    _flush()
    return marked
