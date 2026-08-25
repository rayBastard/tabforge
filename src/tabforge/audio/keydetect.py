"""
Key detection: Krumhansl-Schmuckler profile correlation over a chroma vector.

The scoring is pure Python over 12 numbers (no numpy), so the core is
testable without audio; detect_key() wraps it with librosa's chroma_cqt.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

PITCH_NAMES = ("C", "C#", "D", "D#", "E", "F",
               "F#", "G", "G#", "A", "A#", "B")

# Krumhansl-Schmuckler tone profiles (probe-tone ratings), index 0 = tonic.
MAJOR_PROFILE = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
                 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
MINOR_PROFILE = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
                 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)

# Accidentals in the key signature (positive = sharps, negative = flats),
# indexed by tonic pitch class. Enharmonic spellings pick the smaller count.
# A minor key shares its signature with the relative major three semitones
# up, so one table serves both.
_MAJOR_ACCIDENTALS = (0, -5, 2, -3, 4, -1, 6, 1, -4, 3, -2, 5)


@dataclass(slots=True)
class Key:
    tonic: int          # pitch class, 0 = C
    minor: bool
    correlation: float  # Pearson r of the winning profile

    @property
    def name(self) -> str:
        return f"{PITCH_NAMES[self.tonic]} {'minor' if self.minor else 'major'}"

    @property
    def accidentals(self) -> int:
        tonic = (self.tonic + 3) % 12 if self.minor else self.tonic
        return _MAJOR_ACCIDENTALS[tonic]


def _pearson(a: Sequence[float], b: Sequence[float]) -> float:
    n = len(a)
    ma = sum(a) / n
    mb = sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va == 0 or vb == 0:
        return 0.0
    return cov / (va * vb) ** 0.5


def detect_key_from_chroma(chroma: Sequence[float]) -> Key:
    """12 chroma energies (index 0 = C) -> the best of 24 keys."""
    if len(chroma) != 12:
        raise ValueError("chroma must have 12 values")
    values = [float(c) for c in chroma]
    best = Key(0, False, -2.0)
    for tonic in range(12):
        rotated = values[tonic:] + values[:tonic]
        for minor, profile in ((False, MAJOR_PROFILE), (True, MINOR_PROFILE)):
            r = _pearson(rotated, profile)
            if r > best.correlation:
                best = Key(tonic, minor, r)
    return best


def detect_key(audio: Path, audio_data: tuple | None = None) -> Key:
    """Wav/mp3 -> Key, via the time-averaged chroma_cqt.
    audio_data: optional preloaded (y, sr) from transcribe.load_audio."""
    import librosa
    import numpy as np

    if audio_data is not None:
        y, sr = audio_data
    else:
        y, sr = librosa.load(str(audio), mono=True)
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    return detect_key_from_chroma(np.mean(chroma, axis=1).tolist())
