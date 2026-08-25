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
