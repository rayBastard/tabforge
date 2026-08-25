"""
Round-trip check for the .gp5 export.

Reads a .gp5 back through PyGuitarPro and compares it against the MIDI file
written from the same shapes: note count and the multiset of pitches must
match exactly (a gp5 note's pitch = open-string value + fret).

Usage:
    python scripts/check_gp5.py [path/to/file.gp5] [path/to/file.mid]

Defaults to result/bass/bass.gp5 and result/bass/bass.mid.
Exits non-zero on any discrepancy.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path


def gp5_pitches(path: Path) -> tuple[list[int], int, int]:
    """Returns (pitches, beat_count, impossible_beat_count)."""
    import guitarpro as gp

    song = gp.parse(str(path))
    track = song.tracks[0]
    string_value = {s.number: s.value for s in track.strings}

    pitches: list[int] = []
    beats = 0
    impossible = 0
    for measure in track.measures:
        for voice in measure.voices:
            for beat in voice.beats:
                beats += 1
                strings = [n.string for n in beat.notes]
                if len(strings) != len(set(strings)):
                    impossible += 1
                for note in beat.notes:
                    pitches.append(string_value[note.string] + note.value)
    return pitches, beats, impossible


def midi_pitches(path: Path) -> list[int]:
    import pretty_midi

    pm = pretty_midi.PrettyMIDI(str(path))
    return [note.pitch for inst in pm.instruments for note in inst.notes]


def main() -> int:
    gp5_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("result/bass/bass.gp5")
    mid_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("result/bass/bass.mid")

    for p in (gp5_path, mid_path):
        if not p.is_file():
            print(f"FAIL: {p} not found — run the pipeline first")
            return 1

    got, beats, impossible = gp5_pitches(gp5_path)
    want = midi_pitches(mid_path)

    ok = True
    print(f"{gp5_path}: {len(got)} notes in {beats} beats")
    print(f"{mid_path}: {len(want)} notes")

    if impossible:
        ok = False
        print(f"FAIL: {impossible} beats have two notes on the same string")

    if len(got) != len(want):
        ok = False
        print(f"FAIL: note count mismatch (gp5 {len(got)} vs midi {len(want)})")

    diff = Counter(want) - Counter(got)
    extra = Counter(got) - Counter(want)
    if diff or extra:
        ok = False
        if diff:
            print(f"FAIL: pitches missing from gp5: {dict(diff)}")
        if extra:
            print(f"FAIL: unexpected pitches in gp5: {dict(extra)}")
    else:
        print("pitch multisets match")

    print("OK" if ok else "MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
