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

# thresholds tuned on the golden corpus (docs/eval.md, task 54):
# healthy margins on all 18 card decisions across the three tracks
GUITAR_MIN = 0.4        # Loken 0.80 / Hero 0.58 vs Fulgrim phantom 0.21
VOICE_MIN = 0.25        # Hero 0.69 / Loken 0.53 vs Fulgrim 0.13
DRUMS_MIN = 0.15        # Loken 0.38 / Hero 0.55 vs Fulgrim 0.03
PIANO_MIN = 0.10
# measured on the 30 s analyze sample: real bass 0.006 (Loken) /
# 0.047 (Hero) vs Fulgrim's phantom (piano left hand) 0.135
BASS_MAX_LEAK = 0.10

MT3_TIMEOUT_S = 3600    # ~1x realtime on CPU; a 6-min track takes ~6 min

_SELF_TAGS = {
    "guitar": ("Guitar", "Electric guitar", "Acoustic guitar"),
    "vocals": ("Singing", "Speech", "Rapping"),
    "drums": ("Drum kit", "Drum", "Snare drum"),
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
    progress("analyze", "MT3 arbiter: listening to the whole mix "
                        "(~1x realtime, first run loads the model)")
    try:
        subprocess.run(
            [str(python), str(runner), str(space), str(mix), str(out)],
            check=True, capture_output=True, timeout=MT3_TIMEOUT_S)
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


def _bass_leak_share(stems: dict[str, Path], sample_s: float = 30.0
                     ) -> float:
    """Share of the bass stem's sampled notes whose harmonics are
    stronger in another stem — phantom bass (bleed) scores high."""
    from ..core import NoteEvent
    from . import transcribe as T
    from .validate import _StemSpectra, filter_leaked_notes

    bass = stems.get("bass")
    if bass is None:
        return 0.0
    import soundfile as sf

    info = sf.info(str(bass))
    total_s = info.frames / info.samplerate
    start_s = max(0.0, (total_s - sample_s) / 2)
    target = bass
    if total_s > sample_s + 2:
        frames = int(info.samplerate * sample_s)
        data, sr = sf.read(str(bass), start=int(start_s * info.samplerate),
                           frames=frames, always_2d=True)
        target = bass.parent / "_arbiter_bass.wav"
        sf.write(str(target), data, sr)
    notes = T.cleanup(T.transcribe_stem(
        target, **T.PRESETS.get("bass", {})), max_polyphony=1)
    if target is not bass:
        notes = [NoteEvent(n.pitch, n.start + start_s, n.duration,
                           n.velocity) for n in notes]
        target.unlink(missing_ok=True)
    if not notes:
        return 0.0
    spectra = _StemSpectra(stems)
    kept = filter_leaked_notes(notes, "bass", stems, spectra, margin=2.0)
    return 1.0 - len(kept) / len(notes)


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
    from .tagging import tag_probs

    def evidence_for(stem: str):
        def check() -> bool:
            if stem == "bass":
                return _bass_leak_share(stems) <= BASS_MAX_LEAK
            wav = stems.get(stem)
            if wav is None:
                return True     # nothing to test — do not uncheck
            probs = tag_probs(wav, _SELF_TAGS[stem])
            if not probs:
                return True     # tagger off/unavailable: benefit of doubt
            if stem == "vocals":
                return sum(probs.values()) >= VOICE_MIN
            floor = {"guitar": GUITAR_MIN, "drums": DRUMS_MIN,
                     "piano": PIANO_MIN}[stem]
            return max(probs.values()) >= floor
        return check

    out = {}
    for stem, status in statuses.items():
        out[stem] = judge(stem, density.get(stem, 0.0), status,
                          evidence_for(stem))
        progress("analyze", f"{stem}: arbiter says {out[stem]}")
    return out
