"""
Transcription yields notes in seconds. Sheet music needs beats and
durations snapped to a grid. Otherwise the staff turns into a mess
of dotted thirty-second notes.
"""

from __future__ import annotations

from dataclasses import dataclass

from .fretboard import NoteEvent

# Duration in Guitar Pro units: 1=whole, 4=quarter, 8=eighth...
DURATION_VALUES = (1, 2, 4, 8, 16, 32)


@dataclass(slots=True)
class Grid:
    beats: list[float]        # beat times, seconds
    subdivision: int = 4      # divisions per beat (4 = sixteenths)

    @property
    def ticks(self) -> list[float]:
        out: list[float] = []
        for i in range(len(self.beats) - 1):
            a, b = self.beats[i], self.beats[i + 1]
            for k in range(self.subdivision):
                out.append(a + (b - a) * k / self.subdivision)
        out.append(self.beats[-1])
        return out

    def snap(self, t: float) -> tuple[int, float]:
        """Nearest grid tick: (index, time)."""
        ticks = self.ticks
        idx = min(range(len(ticks)), key=lambda i: abs(ticks[i] - t))
        return idx, ticks[idx]


def quantize(notes: list[NoteEvent], grid: Grid,
             strength: float = 1.0) -> list[NoteEvent]:
    """strength=1.0 — hard snap to the grid, 0.5 — halfway (keeps the groove)."""
    out = []
    for n in notes:
        _, snapped = grid.snap(n.start)
        start = n.start + (snapped - n.start) * strength
        _, snapped_end = grid.snap(n.end)
        duration = max(snapped_end - start, _tick_len(grid))
        out.append(NoteEvent(n.pitch, start, duration, n.velocity,
                             list(n.bends)))
    return out


def _tick_len(grid: Grid) -> float:
    if len(grid.beats) < 2:
        return 0.125
    span = (grid.beats[-1] - grid.beats[0]) / (len(grid.beats) - 1)
    return span / grid.subdivision


def duration_symbol(seconds: float, bpm: float) -> tuple[int, bool]:
    """
    Seconds -> (duration value, whether it is dotted).
    Finds the nearest note duration on a logarithmic scale.
    """
    quarter = 60.0 / bpm
    best = (4, False)
    best_err = float("inf")
    for value in DURATION_VALUES:
        for dotted in (False, True):
            length = quarter * (4.0 / value) * (1.5 if dotted else 1.0)
            err = abs(length - seconds) / seconds
            if err < best_err:
                best_err, best = err, (value, dotted)
    return best


def split_measures(notes: list[NoteEvent], grid: Grid,
                   beats_per_measure: int = 4) -> list[list[NoteEvent]]:
    """Distributes notes into measures."""
    if not grid.beats:
        return [notes]
    bounds = grid.beats[::beats_per_measure]
    measures: list[list[NoteEvent]] = [[] for _ in range(max(len(bounds), 1))]
    for n in notes:
        idx = 0
        for i, b in enumerate(bounds):
            if n.start >= b - 1e-6:
                idx = i
        measures[idx].append(n)
    return measures
