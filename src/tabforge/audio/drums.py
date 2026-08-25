"""
Drum transcription: onset detection + spectral classification.

Basic Pitch is a pitched-note model — pointing it at a drum kit yields
nonsense, so percussion gets the opposite decomposition: WHEN did a hit
land (librosa onsets) and WHICH voice of the kit made it (the spectrum
of its first milliseconds). Three energy bands separate a kit well
enough for tab: kicks live below ~150 Hz, snares fill the mids with
noise, cymbals are almost pure treble; a tonal mid-band hit is a tom.
Cymbals split by decay — a closed hi-hat is gone in 50 ms, a crash
rings for half a second.
"""

from __future__ import annotations

from pathlib import Path

from ..core.fretboard import NoteEvent, Placement, Shape, group_into_events

# General MIDI percussion (channel 10) note numbers
KICK = 36
SNARE = 38
HIHAT = 42          # closed
TOM = 47            # mid tom
CRASH = 49

# how much of the stem after each onset the classifier listens to
_SEGMENT_S = 0.30
_ATTACK_S = 0.08

# a hit is a transient: the notated duration is nominal
HIT_DURATION_S = 0.1


def classify_hit(segment, sr: int) -> int:
    """GM percussion note for one hit, from the ~300 ms after its onset."""
    import numpy as np

    attack = segment[: int(_ATTACK_S * sr)]
    if len(attack) < 32:
        return SNARE
    mag = np.abs(np.fft.rfft(attack))
    freqs = np.fft.rfftfreq(len(attack), 1.0 / sr)

    def band(lo: float, hi: float) -> float:
        sel = (freqs >= lo) & (freqs < hi)
        return float(np.sum(mag[sel] ** 2))

    low, mid, high = band(20, 150), band(150, 2000), band(4000, sr / 2)

    if low >= mid and low >= high:
        return KICK
    if high >= mid:
        # cymbal: closed hi-hat dies instantly, a crash keeps ringing
        def rms(x) -> float:
            return float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0
        early = rms(segment[: int(0.05 * sr)])
        late = rms(segment[int(0.15 * sr): int(_SEGMENT_S * sr)])
        return CRASH if early > 0 and late / early > 0.35 else HIHAT
    # mid-dominant: a snare is noise, a tom is a pitch — spectral
    # flatness (geometric/arithmetic mean) tells them apart
    sel = (freqs >= 150) & (freqs < 2000)
    m = mag[sel]
    flatness = float(np.exp(np.mean(np.log(m + 1e-9))) / (np.mean(m) + 1e-9))
    return SNARE if flatness > 0.2 else TOM


def transcribe_drums(wav: Path) -> list[NoteEvent]:
    """Drum hits of a stem as NoteEvents whose pitch is the GM
    percussion number."""
    import librosa
    import numpy as np

    y, sr = librosa.load(str(wav), mono=True)
    if not len(y):
        return []
    oenv = librosa.onset.onset_strength(y=y, sr=sr)
    frames = librosa.onset.onset_detect(onset_envelope=oenv, sr=sr)
    if not len(frames):
        return []
    times = librosa.frames_to_time(frames, sr=sr)
    peak = float(oenv.max()) or 1.0

    hits: list[NoteEvent] = []
    for frame, t in zip(frames, times):
        seg = y[int(t * sr): int((t + _SEGMENT_S) * sr)]
        velocity = int(60 + 67 * min(1.0, float(oenv[frame]) / peak))
        hits.append(NoteEvent(classify_hit(seg, sr), float(t),
                              HIT_DURATION_S, velocity))
    return hits


def drum_shapes(hits: list[NoteEvent],
                tolerance: float = 0.03) -> list[Shape]:
    """Hits as Shapes the writers understand: gp5 stores a percussion
    note as string+fret with the MIDI number in the fret field, so each
    simultaneous voice takes its own string slot (max 6)."""
    shapes: list[Shape] = []
    for event in group_into_events(hits, tolerance):
        voices: dict[int, NoteEvent] = {}
        for hit in event:                      # one voice per GM note
            kept = voices.get(hit.pitch)
            if kept is None or hit.velocity > kept.velocity:
                voices[hit.pitch] = hit
        shape = Shape(start=min(h.start for h in event))
        for i, pitch in enumerate(sorted(voices)):
            if i >= 6:
                break
            shape.placements.append(
                Placement(note=voices[pitch], string=i, fret=pitch))
        shapes.append(shape)
    return shapes


# ASCII drum grid: which staff line carries which GM notes
_GRID_LINES = (
    ("C", frozenset({49, 52, 55, 57})),       # crash / ride family
    ("H", frozenset({42, 44, 46})),           # hi-hats
    ("T", frozenset({41, 43, 45, 47, 48, 50})),
    ("S", frozenset({37, 38, 40})),
    ("K", frozenset({35, 36})),
)
_GRID_WRAP = 32     # events per printed system


def render_drum_ascii(shapes: list[Shape]) -> str:
    """K/S/H grid: one column per event, 'x' where that voice hits."""
    if not shapes:
        return ""
    systems = [shapes[i: i + _GRID_WRAP]
               for i in range(0, len(shapes), _GRID_WRAP)]
    out: list[str] = []
    for system in systems:
        for label, notes in _GRID_LINES:
            marks = "".join(
                "x" if any(p.fret in notes for p in s.placements) else "-"
                for s in system)
            out.append(f"{label}|{marks}|")
        out.append("")
    return "\n".join(out).rstrip() + "\n"
