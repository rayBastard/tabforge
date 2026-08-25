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

    def tick_index(self, t: float) -> int:
        """Grid slot for a time, drift-proof: inside the grid it is the
        nearest REAL tick (the beats follow the audio, however much the
        tempo breathes); beyond the ends it extrapolates linearly by the
        average tick length. Can return a negative index for times before
        the first beat — callers clamp as needed."""
        ticks = self.ticks
        if t <= ticks[0]:
            return -int(round((ticks[0] - t) / _tick_len(self)))
        if t >= ticks[-1]:
            return len(ticks) - 1 + int(round((t - ticks[-1]) / _tick_len(self)))
        return self.snap(t)[0]


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


def gather_chords(notes: list[NoteEvent], window: float = 0.08,
                  max_size: int = 8) -> list[NoteEvent]:
    """Pull the rolled attacks of one chord onto a common onset.

    Piano transcription (and real playing) smears a chord's onsets by
    50-120 ms; quantization then lands the notes on NEIGHBORING ticks and
    a chord plays as a run of jumps. A note joins the current group when
    it starts within `window` of the group's FIRST note (anchored — a fast
    run chains forever, an anchor does not) AND that first note is still
    sounding (a staccato run never gathers). Gathered notes get the
    anchor's start; their ends stay put.
    """
    if not notes:
        return []
    ordered = sorted(notes, key=lambda n: (n.start, n.pitch))
    out: list[NoteEvent] = []
    anchor = ordered[0]
    group = [anchor]
    for n in ordered[1:]:
        if (n.start - anchor.start <= window
                and n.start < anchor.end
                and len(group) < max_size):
            group.append(n)
            continue
        out.extend(_aligned(group, anchor))
        anchor, group = n, [n]
    out.extend(_aligned(group, anchor))
    return out


def _aligned(group: list[NoteEvent], anchor: NoteEvent) -> list[NoteEvent]:
    if len(group) == 1:
        return group
    return [NoteEvent(n.pitch, anchor.start,
                      max(n.end - anchor.start, 0.02),
                      n.velocity, list(n.bends))
            for n in group]


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
