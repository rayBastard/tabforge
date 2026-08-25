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


def separate_stems_roformer(audio: Path, out_dir: Path,
                            cancel_token: object | None = None
                            ) -> dict[str, Path]:
    """BS-Roformer-SW backend (bs-roformer-infer): 6 stems with the same
    names as htdemucs_6s. Runs in-process on CPU; the ~700 MB weights
    download to ~/.cache/bs-roformer-infer on first use. cancel_token is
    accepted for interface parity — in-process inference has no
    subprocess to kill, so cancellation lands at the next checkpoint."""
    import numpy as np
    import soundfile as sf
    import torch
    import yaml
    try:
        from bs_roformer import (demix_track, ensure_model_assets,
                                 get_model_from_config)
        from ml_collections import ConfigDict
    except ImportError as e:
        raise RuntimeError(
            "the roformer separator needs the 'bs-roformer-infer' "
            "package — pip install 'tabforge[roformer]'") from e

    slug = "roformer-model-bs-roformer-sw-by-jarredou"
    ckpt, cfg_path = ensure_model_assets(slug)

    class _Loader(yaml.SafeLoader):
        pass
    _Loader.add_constructor("tag:yaml.org,2002:python/tuple",
                            lambda l, n: l.construct_sequence(n))
    config = ConfigDict(yaml.load(open(cfg_path), Loader=_Loader))
    model = get_model_from_config("bs_roformer", config)
    state = torch.load(str(ckpt), map_location="cpu", weights_only=False)
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    model.load_state_dict(state)
    model.eval()

    mix, sr = sf.read(str(audio))
    if mix.ndim == 1:
        mix = np.stack([mix, mix], axis=-1)
    mixture = torch.tensor(mix.T, dtype=torch.float32)
    with torch.no_grad():
        res, _ = demix_track(config, model, mixture, torch.device("cpu"),
                             None)

    stem_dir = out_dir / "bs_roformer_sw" / audio.stem
    stem_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    for instr in config.training.instruments:
        path = stem_dir / f"{instr}.wav"
        sf.write(str(path), res[instr].T, sr)
        out[instr] = path
    return out


SEPARATORS = {
    "demucs": separate_stems,
    "roformer": separate_stems_roformer,
}


def separate(audio: Path, out_dir: Path, backend: str = "demucs",
             cancel_token: object | None = None) -> dict[str, Path]:
    """Separation behind one interface — the eval stand A/Bs backends."""
    if backend not in SEPARATORS:
        raise ValueError(f"unknown separation backend: {backend} "
                         f"(have: {', '.join(sorted(SEPARATORS))})")
    return SEPARATORS[backend](audio, out_dir, cancel_token=cancel_token)


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
    # min_freq must clear the LOWEST real guitar note, not standard
    # tuning's E2: drop A rides A1 = 55 Hz and an 8-string reaches
    # F#1 = 46 Hz — a 70 Hz floor silently deleted every downtuned
    # rhythm part. 38 Hz covers 8-string drop E; the bass is separated
    # away by demucs, so the floors may overlap.
    "guitar": dict(onset_threshold=0.5, frame_threshold=0.28,
                   min_note_length_ms=100, min_freq=38, max_freq=1400),
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


def smooth_beats(beats: list[float], window: int = 13) -> list[float]:
    """Beat times with the tracker's jitter removed.

    librosa's beat tracker wobbles ±30-50 ms around the true pulse.
    The score grid is built FROM those beats while playback runs at a
    rigid BPM, so every wobble turns a steady performance into notes
    randomly shifted by a slot — 'the rhythm keeps jumping'. Each beat
    time is replaced by a local LINEAR fit over its neighbors: zero-mean
    jitter is averaged away (~3x for the default window) while genuine
    gradual tempo drift — the reason the grid follows the audio at all —
    passes through a linear fit untouched."""
    if len(beats) < 4:
        return beats
    import numpy as np

    b = np.asarray(beats, dtype=float)
    n = len(b)
    half = min(window, n) // 2
    idx = np.arange(n, dtype=float)
    out = np.empty(n)
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        x, y = idx[lo:hi], b[lo:hi]
        xm, ym = x.mean(), y.mean()
        denom = float(((x - xm) ** 2).sum())
        slope = float(((x - xm) * (y - ym)).sum()) / denom if denom else 0.0
        out[i] = ym + slope * (i - xm)
    return [float(t) for t in out]


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


def repair_beats(beats: list[float], skip_ratio: float = 1.6,
                 insert_ratio: float = 0.6) -> list[float]:
    """Fix the tracker's LOCAL glitches without forcing global rigidity.

    The tempo of real (and generated) songs genuinely wanders, so the
    grid must keep following the audio — but the tracker occasionally
    SKIPS a beat (one interval ~2x its neighbors: that 'beat' would get
    double-length slots and every bar after it would be off by a beat)
    or inserts a phantom one. An interval much longer than the local
    median gets evenly spaced beats filled in; one much shorter loses
    the extra beat."""
    if len(beats) < 8:
        return beats
    import numpy as np

    b = list(float(t) for t in beats)
    intervals = np.diff(b)
    local = float(np.median(intervals))
    out: list[float] = [b[0]]
    for t in b[1:]:
        gap = t - out[-1]
        if gap < insert_ratio * local:
            continue                     # phantom beat: drop it
        missing = int(round(gap / local)) - 1
        if gap > skip_ratio * local and missing >= 1:
            step = gap / (missing + 1)   # skipped beats: fill evenly
            for k in range(1, missing + 1):
                out.append(out[-1] + step)
            out.append(t)
        else:
            out.append(t)
    return out


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

    def _finish(bpm: float, beats: list[float], ok: bool):
        if not ok:
            return bpm, beats, ok
        return bpm, smooth_beats(repair_beats(beats)), ok

    if not hypotheses:
        tempo, beats = librosa.beat.beat_track(onset_envelope=oenv, sr=sr,
                                               units="time", trim=False)
        return _finish(*guard_tempo(float(np.atleast_1d(tempo)[0]),
                                    [float(b) for b in beats]))

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
    return _finish(*guard_tempo(best_bpm, best_beats))


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
