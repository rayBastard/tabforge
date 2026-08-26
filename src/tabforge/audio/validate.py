"""
Harmonic leak validation: does this note actually LIVE in this stem?

The stand's number-one disease is leakage — 60-90% of an instrument's
notes surface in a part where they don't belong, because separation
bleeds and Basic Pitch transcribes whatever it is given. The check is
physical: a note claimed by stem X must have most of its energy (f0 and
the first harmonics, at the note's own time) IN stem X. When a foreign
pitched stem carries clearly more of that energy, the note is an echo
of someone else's line — drop it.

The filter needs only the separated AUDIO, not the other stems'
transcriptions, so it runs per stem regardless of what the user picked.
"""

from __future__ import annotations

from pathlib import Path

from ..core.fretboard import NoteEvent

# stems that can legitimately own pitched notes and thus compete
PITCHED = ("guitar", "bass", "piano", "vocals", "other")

N_FFT = 4096
HOP = 1024
HARMONICS = 4
MAX_WINDOW_S = 0.6      # a long note's tail says little about its onset


class _StemSpectra:
    """Magnitude spectrograms of every pitched stem, computed lazily
    and shared across the per-stem filter calls of one job."""

    def __init__(self, stems: dict[str, Path]):
        self.paths = {k: v for k, v in stems.items() if k in PITCHED}
        self._cache: dict[str, tuple] = {}

    def get(self, stem: str):
        if stem not in self._cache:
            import librosa
            import numpy as np

            y, sr = librosa.load(str(self.paths[stem]), mono=True)
            mag = np.abs(librosa.stft(y, n_fft=N_FFT, hop_length=HOP))
            self._cache[stem] = (mag ** 2, sr)
        return self._cache[stem]

    def energy(self, stem: str, f0: float, start: float, dur: float) -> float:
        import numpy as np

        power, sr = self.get(stem)
        t0 = int(start * sr / HOP)
        t1 = max(t0 + 1, int((start + min(dur, MAX_WINDOW_S)) * sr / HOP))
        t0 = min(t0, power.shape[1] - 1)
        t1 = min(t1, power.shape[1])
        total = 0.0
        for k in range(1, HARMONICS + 1):
            b = int(round(f0 * k * N_FFT / sr))
            if b + 1 >= power.shape[0]:
                break
            total += float(power[b - 1: b + 2, t0:t1].sum()) / k
        return total


def note_confidences(notes: list[NoteEvent], stem: str,
                     stems: dict[str, Path],
                     spectra: _StemSpectra | None = None) -> list[float]:
    """0..1 confidence per note (task 55): how loudly it was played
    (velocity) blended with how firmly the audio evidence supports it —
    the share of harmonic energy at the note's time/pitch that lives in
    its OWN stem rather than a rival's. Dead notes (recitative crosses)
    ride on velocity alone: they claim rhythm, not pitch."""
    if not notes:
        return []
    vels = [min(1.0, n.velocity / 110.0) for n in notes]
    rivals = ([s for s in PITCHED if s != stem and s in stems]
              if stem in PITCHED else [])
    if not rivals or spectra is None:
        return vels
    out = []
    for n, vel in zip(notes, vels):
        if n.dead:
            out.append(vel)
            continue
        f0 = 440.0 * 2 ** ((n.pitch - 69) / 12)
        home = spectra.energy(stem, f0, n.start, n.duration)
        strongest = max(spectra.energy(r, f0, n.start, n.duration)
                        for r in rivals)
        total = home + strongest
        support = 1.0 if total < 1e-6 else home / total
        out.append(round(0.4 * vel + 0.6 * support, 3))
    return out


def filter_leaked_notes(notes: list[NoteEvent], stem: str,
                        stems: dict[str, Path],
                        spectra: _StemSpectra | None = None,
                        margin: float = 2.0) -> list[NoteEvent]:
    """Notes of `stem` that the audio evidence supports. A note is
    dropped when some OTHER pitched stem holds more than `margin` times
    the harmonic energy this stem holds at the note's time and pitch."""
    if not notes or stem not in PITCHED:
        return notes
    rivals = [s for s in PITCHED if s != stem and s in stems]
    if not rivals:
        return notes
    spectra = spectra or _StemSpectra(stems)

    kept: list[NoteEvent] = []
    for n in notes:
        f0 = 440.0 * 2 ** ((n.pitch - 69) / 12)
        home = spectra.energy(stem, f0, n.start, n.duration)
        strongest = max(spectra.energy(r, f0, n.start, n.duration)
                        for r in rivals)
        # near-silence everywhere is a tie, not evidence of leakage
        if strongest < 1e-6 or strongest <= margin * home:
            kept.append(n)
    return kept
