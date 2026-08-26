"""
MT3 instrument-presence arbiter (task 54).

YourMT3+ hears the WHOLE mix and reports notes with instrument labels.
As a transcriber it loses badly on heavy Suno material (Loken guitar:
12 notes of 6890 — measured, docs/eval.md), but its attribution is
truthful where it hears at all (it named Fulgrim a piano piece while
demucs+BP invented hundreds of guitar notes there). So its product
role is a VERDICT per instrument card: found / absent / uncertain.

The decision needs three signals, because MT3 silence is ambiguous:

  density   MT3 notes/min for the card's classes — loud and clear
            presence ("found").
  self-tags PANNs probabilities that a stem sounds like ITSELF
            (Loken guitar stem: Guitar 0.80; Fulgrim's phantom guitar
            stem — orchestra bleed shaped like a guitar: 0.21).
  leak      for bass, where PANNs cannot tell synth bass from bleed
            (Bass guitar prob 0.02 on REAL Hero bass): the share of
            the stem's sample notes whose harmonics live in another
            stem (Fulgrim phantom 0.41 vs real 0.04-0.06).

Blindness guard: MT3 near-silent on an energetic stem that still
sounds like itself -> "uncertain", the card STAYS CHECKED (Loken's
metal guitar must survive). Energetic but alien -> "absent", the card
is unchecked by default but the user can re-enable it.

Wholly optional: the arbiter activates only when TABFORGE_MT3_DIR
points at a YourMT3+ install (see scripts/mt3_experiment/README.md);
without it analyze behaves exactly as before.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

# notes/min that count as unambiguous MT3 presence
FOUND_DENSITY = {"drums": 60.0}
FOUND_DENSITY_DEFAULT = 20.0

# thresholds tuned on the golden corpus (docs/eval.md, task 54) and
# REVALIDATED on a second, fresh separation of each track: PANNs tag
# probabilities on bleed stems proved unstable across demucs runs
# (Fulgrim's phantom guitar: Guitar 0.21 on one separation, 0.45 on
# the next), so guitar and drums use CONTENT matching against MT3's
# own mix-level transcription instead — deterministic and stable
# (phantom guitar foreign-match 0.36/0.37 on two separations).
GUITAR_FOREIGN_MAX = 0.25   # phantom 0.36-0.37 vs real 0.05-0.12:
                            # "MT3 heard this stem's melody — and filed
                            # it under another instrument" = bleed
DRUMS_OWN_MIN = 0.6         # real kits 0.93-0.98 (MT3 covers the hits)
                            # vs Fulgrim's phantom 0.28-0.31
VOICE_MIN = 0.25        # speech-family tags ARE stable (semantic, not
                        # timbral): Hero 0.69 / Loken 0.53 vs 0.13
PIANO_MIN = 0.10
# MEDIAN leak share over three 30 s windows at filter margin 1.2:
# real bass 0.03 (Loken) / 0.07 (Hero — one dirty section outlier at
# 0.26 is exactly what the median absorbs) vs Fulgrim's phantom
# (piano left hand) 0.43/0.45 on two independent separations
BASS_MAX_LEAK = 0.20

MT3_TIMEOUT_S = 3600    # ~1x realtime on CPU; a 6-min track takes ~6 min

_SELF_TAGS = {
    "vocals": ("Singing", "Speech", "Rapping"),
    "piano": ("Piano", "Electric piano"),
}


def _mt3_card(program: int, is_drum: bool) -> str:
    """GM program of an MT3 note -> the analyze card it argues for."""
    if is_drum:
        return "drums"
    if program <= 7:
        return "piano"
    if 24 <= program <= 31:
        return "guitar"
    if 32 <= program <= 39:
        return "bass"
    if 52 <= program <= 54:
        return "vocals"
    return "other"          # strings, brass, synth, everything else


# default install locations probed when TABFORGE_MT3_DIR is unset —
# a Finder-launched app gets no shell environment, so the arbiter must
# find a standard install on its own
_DEFAULT_MT3_DIRS = ("~/mt3", "~/.tabforge/mt3")


def find_mt3() -> tuple[Path, Path] | None:
    """(ymt3space, venv python) when a YourMT3+ install is present.
    TABFORGE_MT3_DIR wins; otherwise the default locations are probed."""
    env = os.environ.get("TABFORGE_MT3_DIR")
    roots = [env] if env else list(_DEFAULT_MT3_DIRS)
    for root in roots:
        root = Path(root).expanduser()
        space = root / "ymt3space"
        python = Path(os.environ.get(
            "TABFORGE_MT3_PYTHON", root / "venv-mt3" / "bin" / "python"))
        if space.is_dir() and python.exists():
            return space, python
    return None


def run_mt3(mix: Path, work_dir: Path,
            progress=lambda *_: None) -> Path | None:
    """Transcribe the mix with YourMT3+ (subprocess in ITS venv).
    Cached: an existing work_dir/mt3.mid is reused."""
    out = work_dir / "mt3.mid"
    if out.exists():
        return out
    found = find_mt3()
    if found is None:
        return None
    space, python = found
    runner = Path(__file__).with_name("_mt3_run.py")
    if not runner.exists():
        # frozen app: modules live inside the PyInstaller archive, but
        # the runner is shipped as a data file next to the bundle root
        import sys
        base = Path(getattr(sys, "_MEIPASS", ""))
        runner = base / "tabforge" / "audio" / "_mt3_run.py"
    if not runner.exists():
        progress("analyze", "MT3 arbiter: runner script missing from "
                            "this build — skipping verdicts")
        return None
    progress("analyze", "MT3 arbiter: listening to the mix "
                        "(~1x track length)")
    # a PyInstaller-launched parent leaks loader variables that would
    # poison the OUTSIDE venv python (wrong dylibs, wrong stdlib)
    env = {k: v for k, v in os.environ.items()
           if k not in ("DYLD_LIBRARY_PATH", "LD_LIBRARY_PATH",
                        "PYTHONPATH", "PYTHONHOME", "_MEIPASS2")}
    try:
        subprocess.run(
            [str(python), str(runner), str(space), str(mix), str(out)],
            check=True, capture_output=True, timeout=MT3_TIMEOUT_S,
            env=env)
    except (subprocess.SubprocessError, OSError):
        progress("analyze", "MT3 arbiter unavailable — cards keep "
                            "their RMS-based statuses")
        return None
    return out if out.exists() else None


def mt3_densities(midi: Path, duration_min: float) -> dict[str, float]:
    """MT3 notes per minute, per analyze card."""
    import pretty_midi

    counts: dict[str, int] = {}
    pm = pretty_midi.PrettyMIDI(str(midi))
    for track in pm.instruments:
        card = _mt3_card(track.program, track.is_drum)
        counts[card] = counts.get(card, 0) + len(track.notes)
    minutes = max(duration_min, 0.1)
    return {card: n / minutes for card, n in counts.items()}


def mt3_card_notes(midi: Path, card: str):
    """Full NoteEvents of one analyze card from the cached whole-mix
    MT3 transcription — the "mt3" note_source (task 57). None when the
    cache is missing or MT3 heard nothing for the card."""
    if not midi.exists():
        return None
    import pretty_midi

    from ..core import NoteEvent

    out = []
    pm = pretty_midi.PrettyMIDI(str(midi))
    for track in pm.instruments:
        if _mt3_card(track.program, track.is_drum) != card:
            continue
        out.extend(NoteEvent(n.pitch, n.start,
                             max(n.end - n.start, 0.05), n.velocity)
                   for n in track.notes)
    out.sort(key=lambda n: n.start)
    return out or None


def mt3_note_pools(midi: Path) -> dict[str, list[tuple[int, float]]]:
    """(pitch, onset) pairs per analyze card from the MT3 MIDI."""
    import pretty_midi

    pools: dict[str, list[tuple[int, float]]] = {}
    pm = pretty_midi.PrettyMIDI(str(midi))
    for track in pm.instruments:
        card = _mt3_card(track.program, track.is_drum)
        pools.setdefault(card, []).extend(
            (n.pitch, n.start) for n in track.notes)
    return pools


def _sample_notes(wav: Path, preset: dict, max_polyphony: int = 6,
                  sample_s: float = 30.0, center: float = 0.5) -> list:
    """Basic Pitch on sample_s of a stem around `center` (0..1 of the
    track), note times shifted back to the ABSOLUTE track timeline."""
    from ..core import NoteEvent
    from . import transcribe as T

    import soundfile as sf

    info = sf.info(str(wav))
    total_s = info.frames / info.samplerate
    start_s = min(max(0.0, total_s * center - sample_s / 2),
                  max(0.0, total_s - sample_s))
    target = wav
    if total_s > sample_s + 2:
        frames = int(info.samplerate * sample_s)
        data, sr = sf.read(str(wav), start=int(start_s * info.samplerate),
                           frames=frames, always_2d=True)
        target = wav.parent / "_arbiter_sample.wav"
        sf.write(str(target), data, sr)
    notes = T.cleanup(T.transcribe_stem(target, **preset),
                      max_polyphony=max_polyphony)
    if target is not wav:
        notes = [NoteEvent(n.pitch, n.start + start_s, n.duration,
                           n.velocity) for n in notes]
        target.unlink(missing_ok=True)
    return notes


def _bass_leak_share(stems: dict[str, Path]) -> float:
    """Share of the bass stem's sampled notes whose harmonics are
    stronger in another stem — phantom bass (bleed) scores high.

    The MEDIAN of three sample windows: a phantom is a phantom all the
    way through (0.41-0.67 in every off-center window), while a single
    window proved vulnerable both to demucs's run-to-run variance (the
    user's live Fulgrim run slipped under a mid-window threshold) and
    to one locally dirty section of a REAL bass stem (Hero at 2/3:
    0.26 against 0.01-0.11 everywhere else)."""
    import statistics

    from . import transcribe as T
    from .validate import _StemSpectra, filter_leaked_notes

    bass = stems.get("bass")
    if bass is None:
        return 0.0
    spectra = _StemSpectra(stems)
    shares = []
    for center in (0.25, 0.5, 0.75):
        notes = _sample_notes(bass, T.PRESETS.get("bass", {}),
                              max_polyphony=1, center=center)
        if not notes:
            continue
        kept = filter_leaked_notes(notes, "bass", stems, spectra,
                                   margin=1.2)
        shares.append(1.0 - len(kept) / len(notes))
    return statistics.median(shares) if shares else 0.0


def _guitar_foreign_match(stems: dict[str, Path],
                          pools: dict, tol: float = 0.1) -> float:
    """Share of the guitar stem's sampled notes that MT3 heard in the
    mix and filed under ANOTHER pitched instrument (time + pitch-class
    match). High = the stem is that instrument's bleed, shaped like a
    guitar by demucs; low = MT3 simply did not hear this material
    (its distorted-guitar blindness). Stable where PANNs tags are not:
    0.36/0.37 on two separations of the Fulgrim phantom vs 0.05 (Loken)
    and 0.12 (Hero) for real guitar."""
    from . import transcribe as T

    wav = stems.get("guitar")
    if wav is None:
        return 0.0
    notes = _sample_notes(wav, T.PRESETS.get("guitar", {}))
    if not notes:
        return 0.0
    foreign = [x for card, pool in pools.items()
               if card not in ("guitar", "drums") for x in pool]
    hits = sum(
        1 for n in notes
        if any(abs(s - n.start) <= tol and (p - n.pitch) % 12 == 0
               for p, s in foreign))
    return hits / len(notes)


def _drums_own_match(stems: dict[str, Path], pools: dict,
                     sample_s: float = 30.0, tol: float = 0.1) -> float:
    """Share of the drum stem's onsets that MT3's own DRUM notes cover.
    A real kit is covered even when MT3 undercounts it (0.93-0.98);
    Fulgrim's phantom drum stem (piano attacks and hiss) is not
    (0.28-0.31)."""
    import librosa
    import numpy as np

    wav = stems.get("drums")
    if wav is None:
        return 0.0
    y, sr = librosa.load(str(wav), mono=True)
    mid = len(y) // 2
    half = int(sample_s * sr / 2)
    off = max(0, mid - half) / sr
    onsets = librosa.onset.onset_detect(
        y=y[max(0, mid - half): mid + half], sr=sr, units="time") + off
    own = np.array(sorted(s for _, s in pools.get("drums", [])))
    if not len(onsets) or not len(own):
        return 0.0
    return float(np.mean([np.min(np.abs(own - t)) <= tol
                          for t in onsets]))


def judge(stem: str, density: float, rms_status: str,
          self_evidence) -> str:
    """One card's verdict. self_evidence() is called lazily — it is
    the expensive branch (PANNs / a Basic Pitch sample)."""
    if rms_status == "absent":
        return "absent"
    if density >= FOUND_DENSITY.get(stem, FOUND_DENSITY_DEFAULT):
        return "found"
    if stem == "other":
        # the catch-basin has no self-identity to test — never
        # auto-uncheck what we cannot name
        return "uncertain"
    return "uncertain" if self_evidence() else "absent"


def verdicts(mix: Path, stems: dict[str, Path],
             statuses: dict[str, str], duration_min: float,
             work_dir: Path,
             progress=lambda *_: None) -> dict[str, str] | None:
    """Verdict per card, or None when no MT3 install is configured."""
    midi = run_mt3(mix, work_dir, progress)
    if midi is None:
        return None
    density = mt3_densities(midi, duration_min)
    pools = mt3_note_pools(midi)
    from .tagging import tag_probs

    def evidence_for(stem: str):
        def check() -> bool:
            if stem == "bass":
                return _bass_leak_share(stems) <= BASS_MAX_LEAK
            if stem == "guitar":
                return (_guitar_foreign_match(stems, pools)
                        < GUITAR_FOREIGN_MAX)
            if stem == "drums":
                return _drums_own_match(stems, pools) >= DRUMS_OWN_MIN
            wav = stems.get(stem)
            if wav is None:
                return True     # nothing to test — do not uncheck
            probs = tag_probs(wav, _SELF_TAGS[stem])
            if not probs:
                return True     # tagger off/unavailable: benefit of doubt
            if stem == "vocals":
                return sum(probs.values()) >= VOICE_MIN
            return max(probs.values()) >= PIANO_MIN
        return check

    out = {}
    for stem, status in statuses.items():
        out[stem] = judge(stem, density.get(stem, 0.0), status,
                          evidence_for(stem))
        progress("analyze", f"{stem}: arbiter says {out[stem]}")
    return out
