"""
The low-register pit, treated at the source.

Basic Pitch's frequency resolution collapses below ~100 Hz: adjacent
semitones sit ~4 Hz apart at C2 and short notes never gather enough
periods — hence the missed notes and octave jumps that plague bass,
drop-tuned guitars and low vocals alike. The mirror of that physics is
the cure: shifted an octave UP, the same material lands where the model
works well.

The shift itself is the RESAMPLE TRICK, not a pitch-shifter: the same
samples are declared to be at twice the sample rate, so the audio plays
2x fast and +12 semitones with zero artifacts; the transcribed times
are then doubled and pitches dropped by 12. (librosa's pitch_shift was
compared on the stand: slower and no better — the trick stays.)

Merging the two passes: above the crossover we trust the normal pass,
below it the octave pass, and duplicates (same pitch, onsets within
50 ms) collapse into the louder observation.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from ..core.fretboard import NoteEvent

LOW_TRUST = 45      # below A2: the octave pass knows better
HIGH_TRUST = 48     # above C3: the normal pass knows better
DEDUP_ONSET_S = 0.05


def _octave_pass(wav: Path, preset: dict) -> list[NoteEvent]:
    """Transcribe the same audio declared at 2x sample rate: +12
    semitones, half duration — then undo both in the note list."""
    import soundfile as sf

    from . import transcribe as T

    data, sr = sf.read(str(wav))
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        sf.write(f.name, data, sr * 2)
        fast = Path(f.name)
    try:
        # the frequency floor/ceiling must travel up with the audio
        shifted = dict(preset)
        for key in ("min_freq", "max_freq"):
            if shifted.get(key):
                shifted[key] = shifted[key] * 2
        notes = T.transcribe_stem(fast, **shifted)
    finally:
        fast.unlink(missing_ok=True)
    return [NoteEvent(n.pitch - 12, n.start * 2, n.duration * 2,
                      n.velocity, list(n.bends))
            for n in notes]


def transcribe_with_low_pass(wav: Path, preset: dict) -> list[NoteEvent]:
    """Two-pass transcription: the normal pass owns the top, the
    octave-shifted pass owns the bottom, the crossover zone goes to the
    louder observation."""
    from . import transcribe as T

    normal = T.transcribe_stem(wav, **preset)
    low = _octave_pass(wav, preset)

    merged: list[NoteEvent] = []
    merged += [n for n in normal if n.pitch >= HIGH_TRUST]
    merged += [n for n in low if n.pitch <= LOW_TRUST]
    # the crossover zone: both passes are heard, louder wins per event
    zone = ([n for n in normal if LOW_TRUST < n.pitch < HIGH_TRUST]
            + [n for n in low if LOW_TRUST < n.pitch < HIGH_TRUST])
    merged += zone

    # collapse duplicates (same pitch, onsets within the window),
    # keeping the louder observation
    merged.sort(key=lambda n: (n.pitch, n.start))
    out: list[NoteEvent] = []
    for n in merged:
        prev = out[-1] if out else None
        if (prev is not None and prev.pitch == n.pitch
                and abs(prev.start - n.start) <= DEDUP_ONSET_S):
            if n.velocity > prev.velocity:
                out[-1] = n             # the louder observation wins
            continue
        out.append(n)
    out.sort(key=lambda n: (n.start, n.pitch))
    return out
