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


def gp5_notes(path: Path) -> tuple[list[tuple[float, int]], int, int]:
    """Returns ([(time_in_quarters, pitch)], beat_count, impossible_beats),
    via the shared reader in tabforge.export.gp5_read."""
    from tabforge.export.gp5_read import read_gp5

    contents = read_gp5(str(path))
    return contents.notes, len(contents.beats), contents.impossible_beats


def key_signatures(path: Path) -> list[str]:
    """Key signature name of every measure header — the pipeline writes one
    key, so any mid-song change is an export bug."""
    import guitarpro as gp

    song = gp.parse(str(path))
    return [h.keySignature.name for h in song.measureHeaders]


def midi_notes(path: Path) -> list[tuple[float, int]]:
    """[(time_in_seconds, pitch)]"""
    import pretty_midi

    pm = pretty_midi.PrettyMIDI(str(path))
    return [(note.start, note.pitch)
            for inst in pm.instruments for note in inst.notes]


def timing_deviation(gp5: list[tuple[float, int]],
                     midi: list[tuple[float, int]]) -> float:
    """Max timing deviation in quarter notes, per pitch.

    The gp5 tempo is rounded to an integer (scale drift) and measure 1 is
    anchored at the first detected beat, not second zero (offset), so an
    affine fit seconds->quarters (slope + intercept) is computed and the
    residuals compared. This checks exactly what broken measure assembly
    breaks: relative note positions.
    """
    by_pitch_gp5: dict[int, list[float]] = {}
    by_pitch_midi: dict[int, list[float]] = {}
    for t, p in gp5:
        by_pitch_gp5.setdefault(p, []).append(t)
    for t, p in midi:
        by_pitch_midi.setdefault(p, []).append(t)

    pairs: list[tuple[float, float]] = []      # (midi_seconds, gp5_quarters)
    for p, times in by_pitch_gp5.items():
        other = sorted(by_pitch_midi.get(p, []))
        for a, b in zip(sorted(times), other):
            pairs.append((b, a))
    n = len(pairs)
    if n < 2:
        return 0.0
    mean_s = sum(s for s, _ in pairs) / n
    mean_q = sum(q for _, q in pairs) / n
    den = sum((s - mean_s) ** 2 for s, _ in pairs)
    if den == 0:
        return 0.0
    scale = sum((s - mean_s) * (q - mean_q) for s, q in pairs) / den
    offset = mean_q - scale * mean_s
    return max(abs(q - (s * scale + offset)) for s, q in pairs)


def main() -> int:
    gp5_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("result/bass/bass.gp5")
    mid_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("result/bass/bass.mid")

    for p in (gp5_path, mid_path):
        if not p.is_file():
            print(f"FAIL: {p} not found — run the pipeline first")
            return 1

    got, beats, impossible = gp5_notes(gp5_path)
    want = midi_notes(mid_path)

    ok = True
    print(f"{gp5_path}: {len(got)} notes in {beats} beats")
    print(f"{mid_path}: {len(want)} notes")

    if impossible:
        ok = False
        print(f"FAIL: {impossible} beats have two notes on the same string")

    keys = key_signatures(gp5_path)
    if len(set(keys)) > 1:
        ok = False
        first = keys[0]
        wrong = next(i for i, k in enumerate(keys) if k != first)
        print(f"FAIL: key signature changes mid-song: measure 1 is {first}, "
              f"measure {wrong + 1} is {keys[wrong]}")
    else:
        print(f"key signature: {keys[0]} in all {len(keys)} measures")

    if len(got) != len(want):
        ok = False
        print(f"FAIL: note count mismatch (gp5 {len(got)} vs midi {len(want)})")

    got_p = [p for _, p in got]
    want_p = [p for _, p in want]
    diff = Counter(want_p) - Counter(got_p)
    extra = Counter(got_p) - Counter(want_p)
    if diff or extra:
        ok = False
        if diff:
            print(f"FAIL: pitches missing from gp5: {dict(diff)}")
        if extra:
            print(f"FAIL: unexpected pitches in gp5: {dict(extra)}")
    else:
        print("pitch multisets match")

    if not diff and not extra:
        dev = timing_deviation(got, want)
        # half a beat: a sixteenth of slack for grid rounding plus room
        # for collision-shifted events
        limit = 0.5
        print(f"max timing deviation: {dev:.3f} quarter notes (limit {limit})")
        if dev > limit:
            ok = False
            print("FAIL: note positions drift between gp5 and midi")

    print("OK" if ok else "MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
