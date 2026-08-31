"""
Articulation classification from a note's pitch-bend trajectory.

Pure math over a list of semitone deviations (one per analysis frame,
relative to the nominal pitch), no audio or ML dependencies:

- a monotonic departure of >= 0.7 semitones      -> "slide"
- a single excursion that returns to the start   -> "bend"
- periodic oscillation of +-0.2..0.5 semitones   -> "vibrato"
- anything else (including flat and noise)       -> "none"
"""

from __future__ import annotations

from typing import Sequence

from .fretboard import NoteEvent, group_into_events

SLIDE_MIN = 0.7      # net monotonic departure, semitones
BEND_MIN = 0.3       # minimum excursion depth for a bend
RETURN_TOL = 0.2     # "came back": |end - start| below this
VIBRATO_LO = 0.2     # vibrato amplitude band, semitones
VIBRATO_HI = 0.5
JITTER = 0.05        # frame-to-frame movement below this is noise


def _significant_reversals(deltas: Sequence[float]) -> int:
    """Direction changes of the trajectory, ignoring sub-JITTER noise."""
    directions: list[int] = []
    for d in deltas:
        if abs(d) < JITTER:
            continue
        sign = 1 if d > 0 else -1
        if not directions or directions[-1] != sign:
            directions.append(sign)
    return max(0, len(directions) - 1)


def detect_legato_pairs(
    notes: Sequence[NoteEvent],
    *,
    max_gap: float = 0.06,
    max_overlap: float = 0.03,
    max_interval: int = 4,
    tolerance: float = 0.045,
) -> list[tuple[NoteEvent, NoteEvent, str]]:
    """Find hammer-on / pull-off candidates.

    A pair of consecutive SINGLE notes qualifies when they nearly touch
    (gap under max_gap, or a small overlap), the interval is at most
    max_interval semitones, and the second note is quieter than the first
    (a hammered/pulled note has no pick attack). Ascending pairs are
    "hammer-on", descending "pull-off".

    Returns (first, second, kind) triples referencing the original
    NoteEvent objects, so callers can match them by identity.
    """
    pairs: list[tuple[NoteEvent, NoteEvent, str]] = []
    events = group_into_events(notes, tolerance)
    for a, b in zip(events, events[1:]):
        if len(a) != 1 or len(b) != 1:
            continue                      # chords are picked, not hammered
        first, second = a[0], b[0]
        gap = second.start - first.end
        if not (-max_overlap <= gap < max_gap):
            continue
        interval = second.pitch - first.pitch
        if not (1 <= abs(interval) <= max_interval):
            continue
        if second.velocity >= first.velocity:
            continue
        kind = "hammer-on" if interval > 0 else "pull-off"
        pairs.append((first, second, kind))
    return pairs


def classify_articulation(bends: Sequence[float]) -> str:
    """Classify a pitch-bend trajectory into slide/bend/vibrato/none."""
    if len(bends) < 4:
        return "none"

    b = [float(x) - float(bends[0]) for x in bends]   # relative to onset
    net = b[-1]
    lo, hi = min(b), max(b)
    span = hi - lo
    peak = max(abs(lo), abs(hi))
    reversals = _significant_reversals(
        [b[i + 1] - b[i] for i in range(len(b) - 1)])

    # Slide: keeps going one way and ends >= 0.7 semitones away. "Roughly
    # monotonic" = the whole span is explained by the net movement.
    if abs(net) >= SLIDE_MIN and span <= abs(net) + 2 * JITTER:
        return "slide"

    # Vibrato: several direction changes with amplitude inside the band.
    if reversals >= 3 and VIBRATO_LO <= peak <= VIBRATO_HI:
        return "vibrato"

    # Bend: goes out once (or up-hold-down: <= 2 reversals) deep enough,
    # and comes back to where it started.
    if (reversals <= 2 and peak >= BEND_MIN and abs(net) <= RETURN_TOL):
        return "bend"

    return "none"


def fold_trills(notes: list, bpm: float,
                min_notes: int = 5,
                max_interval: int = 2) -> int:
    """Trills as ornaments, not note walls (2026-08-31): a maximal run
    of SINGLE notes alternating between exactly two pitches at most
    max_interval apart, each IOI faster than the song's own metric
    grid (sextuplets — an ornament outruns the meter; a two-note
    gallop at 16ths must NOT fold), becomes ONE note spanning the run
    with .trill_with set. Mutates `notes` in place (drops the folded
    partners); returns how many trills were written."""
    ioi_max = min(0.11, 60.0 / max(bpm, 1e-6) / 6)
    singles = sorted((n for n in notes if not n.dead),
                     key=lambda n: n.start)
    folded = 0
    drop: set[int] = set()
    i = 0
    while i < len(singles):
        j = i + 1
        a, b = singles[i].pitch, None
        run = [singles[i]]
        while j < len(singles):
            n = singles[j]
            if n.start - run[-1].start > ioi_max:
                break
            if b is None and n.pitch != a \
                    and abs(n.pitch - a) <= max_interval:
                b = n.pitch
            if n.pitch != (a if len(run) % 2 == 0 else b) and \
                    n.pitch != (b if len(run) % 2 == 0 else a):
                break
            if n.pitch not in (a, b):
                break
            run.append(n)
            j += 1
        pitches = {n.pitch for n in run}
        if len(run) >= min_notes and len(pitches) == 2 and b is not None:
            head = run[0]
            head.trill_with = b if head.pitch == a else a
            head.duration = max(head.duration,
                                run[-1].end - head.start)
            for n in run[1:]:
                drop.add(id(n))
            folded += 1
            i = j
        else:
            i += 1
    if drop:
        notes[:] = [n for n in notes if id(n) not in drop]
    return folded
