"""
MIDI input (drop a .mid instead of audio).

The whole second half of the pipeline — beat grid, Viterbi fingering,
exports, the project player, chords — works from notes; a MIDI file IS
notes. So a dropped MIDI (a Suno per-instrument export, a DAW bounce,
our own reference files) becomes a project instantly: no separation,
no transcription, the notes are taken at face value.

Tracks map to our instrument cards by GM program (same mapping the
MT3/MuScriptor sources use); the tempo grid comes from the file's own
tempo map. Suno writes impossible key signatures (9 sharps) that crash
mido — the tolerant patch below shrugs them off, same as the golden
eval loader.
"""

from __future__ import annotations

from pathlib import Path

# Suno's 9-sharp key signatures must not crash the reader
from mido.midifiles import meta as _mido_meta

_orig_keys = dict(_mido_meta._key_signature_decode)


class _TolerantKeys(dict):
    def __missing__(self, key):
        return "C"


_mido_meta._key_signature_decode = _TolerantKeys(_orig_keys)


def is_midi(path: Path) -> bool:
    return path.suffix.lower() in (".mid", ".midi")


def _card_of(program: int, is_drum: bool) -> str:
    from .arbiter import _mt3_card
    return _mt3_card(program, is_drum)


def load_midi_classes(path: Path) -> dict[str, list]:
    """{card: [NoteEvent]} straight from the file."""
    import pretty_midi

    from ..core import NoteEvent

    classes: dict[str, list] = {}
    pm = pretty_midi.PrettyMIDI(str(path))
    for track in pm.instruments:
        card = _card_of(track.program, track.is_drum)
        classes.setdefault(card, []).extend(
            NoteEvent(n.pitch, float(n.start),
                      max(float(n.end - n.start), 0.05), n.velocity)
            for n in track.notes)
    for notes in classes.values():
        notes.sort(key=lambda n: n.start)
    return classes


def midi_project_facts(path: Path) -> tuple[float, list[float], float]:
    """(bpm, beat times, duration_s) from the file's own tempo map."""
    import pretty_midi
    import numpy as np

    pm = pretty_midi.PrettyMIDI(str(path))
    beats = [float(b) for b in pm.get_beats()]
    _times, tempi = pm.get_tempo_changes()
    bpm = float(np.median(tempi)) if len(tempi) else 120.0
    return bpm, beats, float(pm.get_end_time())
