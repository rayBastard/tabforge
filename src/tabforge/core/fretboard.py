"""
The project core: mapping a sequence of notes (pitch + time) to a fingering
on the fretboard — that is, to tablature.

This is NOT solvable greedily by "taking the nearest fret": the choice for the
current note depends on where the hand will be 3 notes later. Hence — dynamic
programming over the whole sequence (the Viterbi algorithm).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Iterable, Sequence

# MIDI numbers of the open strings. Index 0 = the LOWEST string (6th).
TUNINGS: dict[str, tuple[int, ...]] = {
    "standard": (40, 45, 50, 55, 59, 64),      # E2 A2 D3 G3 B3 E4
    "drop_d": (38, 45, 50, 55, 59, 64),
    "eb_standard": (39, 44, 49, 54, 58, 63),   # half step down
    "dadgad": (38, 45, 50, 55, 57, 62),
    "open_g": (38, 43, 50, 55, 59, 62),
    "bass_4": (28, 33, 38, 43),                # E1 A1 D2 G2
    "bass_5": (23, 28, 33, 38, 43),
    "ukulele": (67, 60, 64, 69),
}


@dataclass(slots=True)
class NoteEvent:
    """A note after audio transcription."""
    pitch: int          # MIDI note number
    start: float        # seconds
    duration: float     # seconds
    velocity: int = 96
    # semitone deviations from the nominal pitch, one per analysis frame
    # (empty when the transcriber provides no pitch-bend data)
    bends: list[float] = field(default_factory=list)

    @property
    def end(self) -> float:
        return self.start + self.duration


@dataclass(slots=True)
class Placement:
    """A note assigned to a string and fret."""
    note: NoteEvent
    string: int         # index into tuning, 0 = the lowest
    fret: int


@dataclass(slots=True)
class Shape:
    """One chord/event: a set of simultaneous fingerings."""
    start: float
    placements: list[Placement] = field(default_factory=list)

    @property
    def hand_position(self) -> float:
        """Where the index finger is. Open strings don't count."""
        frets = [p.fret for p in self.placements if p.fret > 0]
        return min(frets) if frets else 0.0

    @property
    def span(self) -> int:
        frets = [p.fret for p in self.placements if p.fret > 0]
        return (max(frets) - min(frets)) if frets else 0


@dataclass(slots=True)
class TabConfig:
    tuning: tuple[int, ...] = TUNINGS["standard"]
    max_fret: int = 22
    reach: int = 3              # frets pos..pos+reach are played without stretching
    max_stretch: int = 5        # maximum stretch, with a penalty
    open_string_bonus: float = 0.35
    high_fret_penalty: float = 0.05   # pulls playing closer to the nut
    stretch_penalty: float = 1.2      # per fret beyond reach
    move_penalty: float = 0.55        # per fret of hand movement
    string_change_penalty: float = 0.10
    legato_bonus: float = 0.8         # legato pair on one string, one position
    beam_width: int = 80
    onset_tolerance: float = 0.045    # notes closer than this = one chord


# ---------------------------------------------------------------------------
# 1. Grouping into events
# ---------------------------------------------------------------------------

def group_into_events(notes: Sequence[NoteEvent], tolerance: float) -> list[list[NoteEvent]]:
    """Notes starting almost simultaneously form a chord."""
    if not notes:
        return []
    ordered = sorted(notes, key=lambda n: (n.start, n.pitch))
    events: list[list[NoteEvent]] = [[ordered[0]]]
    for note in ordered[1:]:
        if note.start - events[-1][0].start <= tolerance:
            events[-1].append(note)
        else:
            events.append([note])
    return events


# ---------------------------------------------------------------------------
# 2. Generating fingering candidates for an event
# ---------------------------------------------------------------------------

def candidates_for_pitch(pitch: int, cfg: TabConfig) -> list[tuple[int, int]]:
    """All (string, fret) pairs producing this pitch."""
    out = []
    for s, open_pitch in enumerate(cfg.tuning):
        fret = pitch - open_pitch
        if 0 <= fret <= cfg.max_fret:
            out.append((s, fret))
    return out


def shapes_for_event(event: Sequence[NoteEvent], cfg: TabConfig) -> list[Shape]:
    """All physically possible ways to play this chord."""
    per_note = [candidates_for_pitch(n.pitch, cfg) for n in event]
    if any(not c for c in per_note):
        # some note is outside the instrument's range — drop it
        keep = [(n, c) for n, c in zip(event, per_note) if c]
        if not keep:
            return []
        event = [n for n, _ in keep]
        per_note = [c for _, c in keep]

    shapes: list[Shape] = []
    for combo in itertools.product(*per_note):
        strings = [s for s, _ in combo]
        if len(set(strings)) != len(strings):
            continue  # two notes on one string — impossible
        frets = [f for _, f in combo if f > 0]
        if frets and max(frets) - min(frets) > cfg.max_stretch:
            continue  # the hand can't stretch that far
        shapes.append(
            Shape(
                start=event[0].start,
                placements=[Placement(n, s, f) for n, (s, f) in zip(event, combo)],
            )
        )
    return shapes


# ---------------------------------------------------------------------------
# 3. Costs. The key idea: the state is not just the fingering but also the
#    hand POSITION. A guitarist keeps the hand in place and works the fingers
#    rather than crawling to each note.
# ---------------------------------------------------------------------------

def positions_for_shape(shape: Shape, cfg: TabConfig) -> list[int]:
    """In which hand positions this chord is playable at all."""
    frets = [p.fret for p in shape.placements if p.fret > 0]
    max_pos = max(0, cfg.max_fret - cfg.reach)
    if not frets:
        return list(range(0, max_pos + 1))       # all open — the hand can be anywhere
    lo, hi = min(frets), max(frets)
    if hi - lo > cfg.max_stretch:
        return []
    first = max(0, hi - cfg.max_stretch)
    last = min(lo, max_pos)
    return list(range(first, last + 1)) or [max(0, min(lo, max_pos))]


def static_cost(shape: Shape, pos: int, cfg: TabConfig) -> float:
    cost = cfg.high_fret_penalty * pos
    for p in shape.placements:
        if p.fret == 0:
            cost -= cfg.open_string_bonus
            continue
        finger = p.fret - pos
        if finger < 0:
            cost += cfg.stretch_penalty * (-finger)          # behind the position
        elif finger > cfg.reach:
            cost += cfg.stretch_penalty * (finger - cfg.reach)
    return cost


def transition_cost(prev: Shape, prev_pos: int, cur: Shape, pos: int,
                    cfg: TabConfig,
                    legato_ids: frozenset | None = None) -> float:
    gap = max(cur.start - prev.start, 1e-3)
    time_factor = 1.0 / (1.0 + 3.0 * gap)    # jumps cost more in a fast passage
    cost = cfg.move_penalty * abs(pos - prev_pos) * time_factor
    prev_strings = {p.string for p in prev.placements}
    cur_strings = {p.string for p in cur.placements}
    cost += cfg.string_change_penalty * len(cur_strings ^ prev_strings) * time_factor
    # A hammer-on/pull-off is only playable on one string without moving
    # the hand — reward exactly that layout. A pair that doesn't land on
    # one string is simply not rewarded, never forced.
    if (legato_ids
            and len(prev.placements) == 1 and len(cur.placements) == 1
            and (id(prev.placements[0].note), id(cur.placements[0].note))
            in legato_ids
            and prev.placements[0].string == cur.placements[0].string
            and pos == prev_pos):
        cost -= cfg.legato_bonus
    return cost


# ---------------------------------------------------------------------------
# 4. Viterbi (beam search) over the event sequence
# ---------------------------------------------------------------------------

def assign_tab(notes: Sequence[NoteEvent], cfg: TabConfig | None = None,
               legato: Sequence[tuple] | None = None) -> list[Shape]:
    """legato: (first_note, second_note, ...) tuples from
    articulation.detect_legato_pairs — pairs are matched by object
    identity and rewarded when they land on one string in one position."""
    cfg = cfg or TabConfig()
    legato_ids = frozenset(
        (id(pair[0]), id(pair[1])) for pair in legato) if legato else None
    events = group_into_events(notes, cfg.onset_tolerance)
    if not events:
        return []

    # state: (cost, shape, hand position, backpointer index)
    State = tuple[float, Shape, int, int]
    history: list[list[State]] = []

    beam: list[State] = []
    for event in events:
        options = [(sh, p) for sh in shapes_for_event(event, cfg)
                   for p in positions_for_shape(sh, cfg)]
        if not options:
            continue
        # An unplayable event (transcription noise, out-of-range cluster)
        # is skipped wherever it occurs — including before the beam is
        # seeded, otherwise one bad first event empties the whole tab.
        if not beam:
            beam = sorted(((static_cost(sh, p, cfg), sh, p, -1)
                           for sh, p in options),
                          key=lambda x: x[0])[: cfg.beam_width]
            history.append(beam)
            continue
        new_beam: list[State] = []
        for shape, pos in options:
            base = static_cost(shape, pos, cfg)
            best_cost, best_idx = float("inf"), 0
            for idx, (acc, prev_shape, prev_pos, _) in enumerate(beam):
                c = acc + base + transition_cost(prev_shape, prev_pos,
                                                shape, pos, cfg, legato_ids)
                if c < best_cost:
                    best_cost, best_idx = c, idx
            new_beam.append((best_cost, shape, pos, best_idx))
        beam = sorted(new_beam, key=lambda x: x[0])[: cfg.beam_width]
        history.append(beam)

    result: list[Shape] = []
    idx = 0
    for step in reversed(range(len(history))):
        _cost, shape, _pos, back = history[step][idx]
        result.append(shape)
        idx = back if back >= 0 else 0
    result.reverse()
    return result


# 5. ASCII tab for a quick eyeball check
# ---------------------------------------------------------------------------

STRING_NAMES = {
    6: ["E", "A", "D", "G", "B", "e"],
    4: ["E", "A", "D", "G"],
    5: ["B", "E", "A", "D", "G"],
}


def render_ascii(shapes: Sequence[Shape], cfg: TabConfig | None = None,
                 wrap: int = 16,
                 legato: Sequence[tuple] | None = None) -> str:
    """ASCII tab. With legato pairs and per-note bend trajectories the
    articulations are drawn too: 5h7 / 7p5 for hammer-on/pull-off,
    / and \\ for slides, ~ after a vibrato note."""
    from .articulation import classify_articulation  # local: avoids a cycle

    cfg = cfg or TabConfig()
    n = len(cfg.tuning)
    names = STRING_NAMES.get(n, [str(i) for i in range(n)])

    # id(first_note) -> (id(second_note), "h"/"p")
    hammer: dict[int, tuple[int, str]] = {}
    for pair in legato or []:
        first, second = pair[0], pair[1]
        kind = pair[2] if len(pair) > 2 else (
            "hammer-on" if second.pitch > first.pitch else "pull-off")
        hammer[id(first)] = (id(second), "h" if kind == "hammer-on" else "p")

    columns: list[list[str]] = []
    for shape in shapes:
        col = ["-"] * n
        for p in shape.placements:
            text = str(p.fret)
            if classify_articulation(p.note.bends) == "vibrato":
                text += "~"
            col[p.string] = text
        width = max(len(c) for c in col)
        columns.append([c.rjust(width, "-") if c != "-" else "-" * width for c in col])

    # separator between consecutive shapes, per string: h/p for a legato
    # pair on one string, / or \ when the earlier note slides
    seps: list[list[str]] = []
    for a, b in zip(shapes, shapes[1:]):
        sep = ["-"] * n
        b_strings = {p.string: p for p in b.placements}
        for p in a.placements:
            if p.string not in b_strings:
                continue
            q = b_strings[p.string]
            if hammer.get(id(p.note), (None,))[0] == id(q.note):
                sep[p.string] = hammer[id(p.note)][1]
            elif p.note.bends and classify_articulation(p.note.bends) == "slide":
                net = p.note.bends[-1] - p.note.bends[0]
                sep[p.string] = "/" if net > 0 else "\\"
        seps.append(sep)

    blocks = []
    for start in range(0, len(columns), wrap):
        chunk = columns[start:start + wrap]
        lines = []
        for s in reversed(range(n)):          # top line = the thinnest string
            parts = [chunk[0][s]]
            for k in range(1, len(chunk)):
                parts.append(seps[start + k - 1][s])
                parts.append(chunk[k][s])
            lines.append(f"{names[s]}|-{''.join(parts)}-|")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
