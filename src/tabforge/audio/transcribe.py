"""
Audio -> notes. We invent NOTHING here: we use state-of-the-art models.

The chain:
  1. Demucs  — split the mix into stems (guitar, bass, vocals, drums)
  2. Basic Pitch (Spotify) — polyphonic transcription of each stem into notes
  3. librosa — tempo and beat grid
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ..core.fretboard import NoteEvent

# Instruments that htdemucs_6s can extract
SIX_STEMS = ("drums", "bass", "other", "vocals", "guitar", "piano")


def separate_stems(audio: Path, out_dir: Path, model: str = "htdemucs_6s") -> dict[str, Path]:
    """Splits the mix into stems. Returns {stem_name: wav_path}."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "demucs", "-n", model, "-o", str(out_dir), str(audio)]
    subprocess.run(cmd, check=True)

    stem_dir = out_dir / model / audio.stem
    return {p.stem: p for p in stem_dir.glob("*.wav")}


def transcribe_stem(
    audio: Path,
    *,
    onset_threshold: float = 0.5,
    frame_threshold: float = 0.3,
    min_note_length_ms: float = 68.0,
    min_freq: float | None = None,
    max_freq: float | None = None,
) -> list[NoteEvent]:
    """One stem -> a list of notes. Thresholds are tuned per instrument."""
    from basic_pitch.inference import predict

    _model_out, _midi, note_events = predict(
        str(audio),
        onset_threshold=onset_threshold,
        frame_threshold=frame_threshold,
        minimum_note_length=min_note_length_ms,
        minimum_frequency=min_freq,
        maximum_frequency=max_freq,
        melodia_trick=True,
    )

    notes = [
        NoteEvent(
            pitch=int(pitch),
            start=float(start),
            duration=max(float(end) - float(start), 0.02),
            velocity=max(1, min(127, int(amplitude * 127))),
        )
        for start, end, pitch, amplitude, *_ in note_events
    ]
    return sorted(notes, key=lambda n: (n.start, n.pitch))


# Threshold presets: bass notes are long and low, lead guitar notes are short
PRESETS: dict[str, dict] = {
    "bass":   dict(onset_threshold=0.45, frame_threshold=0.25,
                   min_note_length_ms=90, min_freq=30, max_freq=400),
    # Tuned on a Suno track (distorted rhythm + lead): a longer minimum note
    # removes ghost fragments, and softer onset/frame thresholds compensate
    # the lost sensitivity and hold chord sustain together. 100 ms still
    # keeps sixteenths up to 150 BPM.
    "guitar": dict(onset_threshold=0.5, frame_threshold=0.28,
                   min_note_length_ms=100, min_freq=70, max_freq=1400),
    "vocals": dict(onset_threshold=0.5, frame_threshold=0.3,
                   min_note_length_ms=90, min_freq=80, max_freq=1200),
    "piano":  dict(onset_threshold=0.5, frame_threshold=0.3,
                   min_note_length_ms=60),
    "other":  dict(),
}


def fold_tempo(bpm: float, lo: float = 70.0, hi: float = 180.0) -> float:
    """Fold a tempo into [lo, hi) by octave shifts (2x / 0.5x)."""
    if bpm <= 0:
        raise ValueError("tempo must be positive")
    if hi < 2 * lo:
        raise ValueError("range must span at least one octave")
    while bpm < lo:
        bpm *= 2.0
    while bpm >= hi:
        bpm /= 2.0
    return bpm


def collapse_tempo_candidates(
    candidates, lo: float = 70.0, hi: float = 180.0, rel_tol: float = 0.02,
) -> list[tuple[float, int]]:
    """Fold candidates into [lo, hi) and merge near-equal values.

    Octave multiples (2x, 1/2x) collapse through the folding itself;
    genuinely different hypotheses — e.g. the classic 4:3 pair 96 vs 128 —
    stay separate entries for the caller to resolve against the audio.
    Returns [(bpm, weight)] sorted by weight (descending), then bpm.
    """
    groups: list[list[float]] = []
    for cand in candidates:
        b = float(cand)
        if b <= 0:
            continue
        b = fold_tempo(b, lo, hi)
        for g in groups:
            rep = sum(g) / len(g)
            if abs(b - rep) <= rel_tol * rep:
                g.append(b)
                break
        else:
            groups.append([b])
    out = [(sum(g) / len(g), len(g)) for g in groups]
    out.sort(key=lambda t: (-t[1], t[0]))
    return out


def detect_tempo(audio: Path) -> tuple[float, list[float]]:
    """Returns (BPM, beat times in seconds).

    beat_track's single estimate flips between tempo multiples from run to
    run, so the tempo is chosen from explicit hypotheses instead: local
    candidates are folded into 70-180 BPM, near-duplicates merged, and 4/3
    and 3/4 alternatives added (the classic beat-tracker confusion). Each
    hypothesis is scored as family weight x mean onset envelope on its beat
    grid: the weight (how often the local periodicity votes for the family;
    stable across runs) decides between different families, the envelope
    decides between same-weight 4:3 variants within one. A raw envelope sum
    would just reward the densest grid, and a raw mean drifts to grids a
    notch too slow that only hit the strongest attacks.
    """
    import librosa
    import numpy as np

    y, sr = librosa.load(str(audio), mono=True)
    oenv = librosa.onset.onset_strength(y=y, sr=sr)
    local = librosa.feature.tempo(onset_envelope=oenv, sr=sr, aggregate=None)
    families = collapse_tempo_candidates(local)

    hypotheses: dict[float, int] = {}
    for bpm, weight in families[:4]:
        for ratio in (1.0, 4 / 3, 3 / 4):
            h = round(fold_tempo(bpm * ratio), 4)
            hypotheses[h] = max(hypotheses.get(h, 0), weight)

    if not hypotheses:
        tempo, beats = librosa.beat.beat_track(onset_envelope=oenv, sr=sr,
                                               units="time", trim=False)
        return float(np.atleast_1d(tempo)[0]), [float(b) for b in beats]

    best_bpm, best_beats, best_score = 0.0, [], -1.0
    for bpm, weight in sorted(hypotheses.items()):
        _t, frames = librosa.beat.beat_track(onset_envelope=oenv, sr=sr,
                                             bpm=bpm, trim=False)
        if len(frames) < 2:
            continue
        score = weight * float(oenv[frames].mean())
        if score > best_score:
            best_score = score
            best_bpm = bpm
            best_beats = [float(t) for t in librosa.frames_to_time(frames, sr=sr)]
    return best_bpm, best_beats


def cleanup(notes: list[NoteEvent], *, min_duration: float = 0.05,
            max_polyphony: int = 6) -> list[NoteEvent]:
    """
    Basic Pitch tends to emit ghost overtones. We remove:
      - fragments that are too short,
      - extra notes in overly dense clusters (keeping the loudest ones).
    """
    notes = [n for n in notes if n.duration >= min_duration]
    notes.sort(key=lambda n: (n.start, -n.velocity))

    out: list[NoteEvent] = []
    i = 0
    while i < len(notes):
        j = i
        while j < len(notes) and notes[j].start - notes[i].start < 0.045:
            j += 1
        chunk = notes[i:j]
        seen: set[int] = set()
        kept = []
        for n in chunk:
            if n.pitch in seen:
                continue
            seen.add(n.pitch)
            kept.append(n)
        out.extend(kept[:max_polyphony])
        i = j
    return out
