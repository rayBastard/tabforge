"""
GAPS guitar backend (task 66) — Riley et al., QMUL.

Code and weights are MIT (a rarity in this project's license safari),
so it runs IN-PROCESS. Its domain is acoustic/classical solo guitar:
on GuitarSet it beats every other backend we have (F1 0.858 vs
MuScriptor-medium 0.745 vs Basic Pitch 0.590), while on distorted
Suno material it loses badly (0.25 vs 0.53 on Loken) — so the "auto"
guitar engine routes to it only for solo tracks that do NOT sound
distorted (the PANNs distortion-family score, already used by the
arbiter guards, is the discriminator).

Install (not a formal extra: its resampy>=0.4.3 pin conflicts with
basic-pitch's <0.4.3 on paper; 0.4.2 runs both fine in practice):

    pip install "git+https://github.com/xavriley/hf_midi_transcription.git"
    pip install "resampy==0.4.2"
"""

from __future__ import annotations

from pathlib import Path

_MODEL = None


def available() -> bool:
    try:
        import hf_midi_transcription  # noqa: F401
        return True
    except ImportError:
        return False


def transcribe_gaps(wav: Path, progress=lambda *_: None) -> list | None:
    """Solo-guitar audio -> NoteEvents, or None without the install.
    The checkpoint (99 MB) downloads from HF on first use; its
    from_pretrained is broken against current huggingface-hub, so the
    constructor is fed a manually downloaded checkpoint."""
    if not available():
        return None
    global _MODEL
    try:
        if _MODEL is None:
            from huggingface_hub import hf_hub_download
            from hf_midi_transcription import MidiTranscriptionModel
            progress("transcribe", "GAPS: loading the acoustic-guitar "
                                   "model (first run downloads 99 MB)")
            ckpt = hf_hub_download("xavriley/midi-transcription-models",
                                   "guitar-gaps.pth")
            _MODEL = MidiTranscriptionModel(instrument="guitar",
                                            checkpoint_path=ckpt)
        import tempfile

        import pretty_midi

        from ..core import NoteEvent

        progress("transcribe", "GAPS: transcribing the guitar")
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "gaps.mid"
            _MODEL.transcribe(str(wav), str(out))
            pm = pretty_midi.PrettyMIDI(str(out))
            notes = [NoteEvent(n.pitch, float(n.start),
                               max(float(n.end - n.start), 0.05),
                               n.velocity)
                     for t in pm.instruments for n in t.notes]
        notes.sort(key=lambda n: n.start)
        return notes or None
    except Exception:  # noqa: BLE001 — optional backend, never fatal
        progress("transcribe", "GAPS unavailable — falling back")
        return None


def sounds_acoustic(mix: Path) -> bool:
    """The domain gate for auto-routing: NOT distortion-flavored.
    Distortion-family sum >= 0.30 meant real electric/metal on every
    measured stem (Loken 0.57, Hero 0.49 vs phantoms 0.11-0.21)."""
    from .tagging import tag_probs

    probs = tag_probs(mix, ("Electric guitar", "Distortion",
                            "Heavy metal"))
    if not probs:
        return False              # no tagger: stay with the incumbent
    return sum(probs.values()) < 0.30
