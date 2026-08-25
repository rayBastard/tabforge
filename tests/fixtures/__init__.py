"""Synthetic wav fixtures: generated on demand with numpy, nothing binary
lives in git. Each generator writes a wav into the given directory and
returns its path.

The corpus exists because one happy track hides edge cases: a short clip
(too few beats), a leading-silence track (grid anchor), a drumless track
(tempo-source fallback), a lone scale with known tempo/key (ground truth),
and silence (the pipeline must return empty results, not crash).
"""

from __future__ import annotations

from pathlib import Path

SR = 22050


def _write(path: Path, y) -> Path:
    import numpy as np
    import soundfile as sf

    peak = float(np.abs(y).max()) if len(y) else 0.0
    if peak > 1.0:
        y = y / peak * 0.9
    sf.write(str(path), y.astype("float32"), SR)
    return path


def _tone(freq: float, dur: float, amp: float = 0.4):
    """A plucked-ish tone: sine + one harmonic with a decay envelope."""
    import numpy as np

    t = np.arange(int(SR * dur)) / SR
    env = np.exp(-3.0 * t)
    return amp * env * (np.sin(2 * np.pi * freq * t)
                        + 0.3 * np.sin(4 * np.pi * freq * t))


def _click(amp: float = 0.8, dur: float = 0.03):
    """A drum-like click: short noise burst with a sharp decay."""
    import numpy as np

    rng = np.random.default_rng(7)
    n = int(SR * dur)
    return amp * rng.standard_normal(n) * np.exp(-np.arange(n) / (n / 6))


def _midi_freq(midi: int) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _place(canvas, start_s: float, clip) -> None:
    i = int(start_s * SR)
    j = min(i + len(clip), len(canvas))
    if i < len(canvas):
        canvas[i:j] += clip[: j - i]


def _note_track(midi_notes, bpm: float, total_s: float, *,
                offset_s: float = 0.0, clicks: bool = True):
    """Notes on quarter beats at the given tempo, optional drum clicks."""
    import numpy as np

    beat = 60.0 / bpm
    y = np.zeros(int(SR * total_s))
    k = 0
    t = offset_s
    while t < total_s - 0.1:
        _place(y, t, _tone(_midi_freq(midi_notes[k % len(midi_notes)]),
                           beat * 0.9))
        if clicks:
            _place(y, t, _click())
        t += beat
        k += 1
    return y


C_MAJOR_SCALE = (60, 62, 64, 65, 67, 69, 71, 72, 71, 69, 67, 65, 64, 62)
GROUND_TRUTH_BPM = 120.0
GROUND_TRUTH_KEY = "C major"


def short_clip(out: Path) -> Path:
    """5 seconds — too few beats for a trustworthy grid."""
    return _write(out / "short_clip.wav",
                  _note_track(C_MAJOR_SCALE, 120.0, 5.0))


def leading_silence(out: Path) -> Path:
    """2 s of silence before the first beat — the grid-anchor case."""
    return _write(out / "leading_silence.wav",
                  _note_track(C_MAJOR_SCALE, 120.0, 22.0, offset_s=2.0))


def drumless(out: Path) -> Path:
    """Melodic content with no percussive clicks at all."""
    return _write(out / "drumless.wav",
                  _note_track(C_MAJOR_SCALE, 100.0, 20.0, clicks=False))


def scale_ground_truth(out: Path) -> Path:
    """A lone C major scale at exactly 120 BPM — end-to-end ground truth."""
    return _write(out / "scale.wav",
                  _note_track(C_MAJOR_SCALE, GROUND_TRUTH_BPM, 25.0))


def silence(out: Path) -> Path:
    """Digital silence: no notes anywhere; the pipeline must return an
    empty result rather than crash. (Even -66 dB noise turned out to be
    loud enough for Basic Pitch to hallucinate notes from.)"""
    import numpy as np

    return _write(out / "silence.wav", np.zeros(int(SR * 8.0)))
