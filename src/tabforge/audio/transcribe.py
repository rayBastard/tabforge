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
import threading
from pathlib import Path
from typing import Sequence

from ..core.fretboard import NoteEvent

# Instruments that htdemucs_6s can extract
SIX_STEMS = ("drums", "bass", "other", "vocals", "guitar", "piano")

# Separations in flight, keyed by the caller's cancel token: a canceled
# job must be able to kill its demucs subprocess instead of waiting
# minutes for the next cooperative checkpoint.
_ACTIVE: dict[object, subprocess.Popen] = {}
_ACTIVE_LOCK = threading.Lock()


def abort_separation(cancel_token: object) -> bool:
    """Kill the demucs subprocess registered under this token, if any."""
    with _ACTIVE_LOCK:
        proc = _ACTIVE.get(cancel_token)
    if proc is None:
        return False
    proc.kill()
    return True


def separate_stems(audio: Path, out_dir: Path, model: str = "htdemucs_6s",
                   cancel_token: object | None = None) -> dict[str, Path]:
    """Splits the mix into stems. Returns {stem_name: wav_path}.

    demucs runs strictly in a subprocess: in-process it reports failure
    via sys.exit(), and SystemExit inherits from BaseException, sailing
    past every `except Exception` up the stack. In a PyInstaller bundle
    sys.executable cannot run `-m demucs`, so the frozen app re-invokes
    itself with a --demucs-worker sentinel (dispatched by the entry
    script) instead.

    cancel_token registers the subprocess so abort_separation(token)
    can kill it mid-run.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    demucs_args = ["-n", model, "-o", str(out_dir), str(audio)]
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--demucs-worker", *demucs_args]
    else:
        cmd = [sys.executable, "-m", "demucs", *demucs_args]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    if cancel_token is not None:
        with _ACTIVE_LOCK:
            _ACTIVE[cancel_token] = proc
    try:
        _out, err = proc.communicate()
    finally:
        if cancel_token is not None:
            with _ACTIVE_LOCK:
                _ACTIVE.pop(cancel_token, None)
    if proc.returncode != 0:
        tail = "\n".join((err or "").strip().splitlines()[-5:])
        raise RuntimeError(
            f"demucs failed with exit code {proc.returncode}:\n{tail}")

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

    # Basic Pitch expresses pitch bends in contour bins, 3 bins = 1 semitone.
    notes = []
    for start, end, pitch, amplitude, *rest in note_events:
        bend_bins = rest[0] if rest and rest[0] is not None else []
        notes.append(NoteEvent(
            pitch=int(pitch),
            start=float(start),
            duration=max(float(end) - float(start), 0.02),
            velocity=max(1, min(127, int(amplitude * 127))),
            bends=[float(b) / 3.0 for b in bend_bins],
        ))
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


def load_audio(audio: Path) -> tuple:
    """Decode+resample once; pass the result to detect_tempo/detect_key
    via audio_data to avoid decoding the same file twice."""
    import librosa

    return librosa.load(str(audio), mono=True)


def ensure_decodable_wav(audio: Path, work_dir: Path) -> Path:
    """demucs' own decoders are stricter than libsndfile: real-world mp3s
    with malformed frames (a Suno download, a rip) make sphn bail with
    'malformed stream', and the bundled desktop app has no ffmpeg to fall
    back to. Anything that isn't already a wav is re-encoded through
    librosa/soundfile, so demucs always receives a clean wav."""
    if audio.suffix.lower() == ".wav":
        return audio
    import librosa
    import soundfile as sf

    y, sr = librosa.load(str(audio), mono=False, sr=None)
    out = work_dir / (audio.stem + ".decoded.wav")
    sf.write(str(out), y.T if y.ndim > 1 else y, int(sr))
    return out


def mix_backing(stems: dict[str, Path], exclude: Sequence[str],
                out: Path) -> Path | None:
    """Sum every stem EXCEPT the excluded ones into a backing track.

    The user transcribes guitar and bass — the backing is everything
    else, to play along with. Peaks are normalized only when the sum
    would clip. Returns None when nothing is left to mix.
    """
    keep = [p for name, p in stems.items() if name not in exclude]
    if not keep:
        return None

    import numpy as np
    import soundfile as sf
    total = None
    sr = None
    for p in keep:
        data, sr = sf.read(str(p), always_2d=True)
        if total is None:
            total = data.copy()
        else:
            if len(data) > len(total):
                total = np.pad(total, ((0, len(data) - len(total)), (0, 0)))
            elif len(total) > len(data):
                data = np.pad(data, ((0, len(total) - len(data)), (0, 0)))
            total += data
    peak = float(np.abs(total).max())
    if peak > 0.99:
        total = total / peak * 0.99
    sf.write(str(out), total, sr)
    return out


def stem_is_audible(wav: Path, rms_threshold: float = 0.005) -> bool:
    """True when the stem carries real signal.

    Calibrated on htdemucs_6s output: a stem that is only residual bleed
    (e.g. the piano stem of a piano-less track) sits around RMS 0.002,
    real content starts an order of magnitude higher.
    """
    import librosa
    import numpy as np

    y, _sr = librosa.load(str(wav), mono=True)
    if not len(y):
        return False
    return float(np.sqrt(np.mean(y ** 2))) >= rms_threshold


FALLBACK_BPM = 120.0


def guard_tempo(bpm: float, beats: list[float],
                min_beats: int = 8,
                lo: float = 40.0, hi: float = 260.0,
                ) -> tuple[float, list[float], bool]:
    """Sanity-checks a tempo estimate.

    Returns (bpm, beats, reliable). A grid with fewer than min_beats beats
    or a BPM outside [lo, hi] is junk (short clip, near-silent stem): fall
    back to a plain 120 with no grid, flagged unreliable so the caller can
    warn the user instead of exporting garbage.
    """
    if len(beats) >= min_beats and lo <= bpm <= hi:
        return bpm, beats, True
    return FALLBACK_BPM, [], False


def detect_tempo(audio: Path,
                 audio_data: tuple | None = None) -> tuple[float, list[float], bool]:
    """Returns (BPM, beat times in seconds, reliable).
    audio_data: optional preloaded (y, sr) from load_audio.

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

    y, sr = audio_data if audio_data is not None else load_audio(audio)
    oenv = librosa.onset.onset_strength(y=y, sr=sr)
    local = librosa.feature.tempo(onset_envelope=oenv, sr=sr, aggregate=None)
    families = collapse_tempo_candidates(local)

    # Top 2 families only: each extra family costs up to 3 beat_track
    # dynamic programs, and the weight ordering is what decides anyway.
    hypotheses: dict[float, int] = {}
    for bpm, weight in families[:2]:
        for ratio in (1.0, 4 / 3, 3 / 4):
            h = round(fold_tempo(bpm * ratio), 4)
            hypotheses[h] = max(hypotheses.get(h, 0), weight)

    if not hypotheses:
        tempo, beats = librosa.beat.beat_track(onset_envelope=oenv, sr=sr,
                                               units="time", trim=False)
        return guard_tempo(float(np.atleast_1d(tempo)[0]),
                           [float(b) for b in beats])

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
    return guard_tempo(best_bpm, best_beats)


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
