"""
Lead/rhythm split for a transcribed guitar part. Works on notes, not audio:
events are scored by register, window polyphony, and attack density, then
the scores are smoothed over a time window so a chord's neighborhood pulls
nearby single notes into the rhythm part.

Starting heuristic: chord events (3+ notes) and their surroundings are
rhythm; single notes in the upper register and dense single-note runs are
lead.
"""

from __future__ import annotations

from typing import Sequence

from .fretboard import NoteEvent, TabConfig, group_into_events

# The chord-grouping window must match the fingering pass, or a "chord"
# could be split across lead and rhythm inconsistently with the tab.
# (TabConfig is slotted, so the default is read off an instance.)
_DEFAULT_TOLERANCE = TabConfig().onset_tolerance


def split_lead_rhythm(
    notes: Sequence[NoteEvent],
    *,
    tolerance: float = _DEFAULT_TOLERANCE,
    window: float = 1.0,
    min_part_fraction: float = 0.1,
) -> tuple[list[NoteEvent], list[NoteEvent]] | None:
    """Returns (lead_notes, rhythm_notes), or None when the material
    doesn't really contain two parts (one side would be < min_part_fraction
    of the notes)."""
    events = group_into_events(notes, tolerance)
    if len(events) < 8:
        return None

    starts = [e[0].start for e in events]
    sizes = [len(e) for e in events]
    means = [sum(n.pitch for n in e) / len(e) for e in events]
    all_pitches = sorted(n.pitch for n in notes)
    register_pivot = all_pitches[len(all_pitches) // 2]  # median pitch

    # Raw per-event rhythm score in [0, 1].
    raw: list[float] = []
    for i, event in enumerate(events):
        if sizes[i] >= 3:
            raw.append(1.0)
            continue
        if sizes[i] == 2:
            raw.append(0.7)
            continue
        score = 0.5
        if means[i] >= register_pivot + 4:
            score -= 0.3          # single note well above the median: lead
        elif means[i] <= register_pivot - 2:
            score += 0.2          # low single note: likely rhythm figure
        # attack density: a fast single-note run is a lick, not strumming
        nearby = sum(1 for s in starts if abs(s - starts[i]) <= 0.5)
        if nearby >= 4:
            score -= 0.2
        raw.append(score)

    # Chords are rhythm unconditionally; smoothing only decides the
    # singles/doubles, with chords pulling their neighborhood toward rhythm.
    labels: list[bool] = []       # True = rhythm
    j0 = 0
    for i in range(len(events)):
        if sizes[i] >= 3:
            labels.append(True)
            continue
        acc = weight = 0.0
        for j in range(j0, len(events)):
            dt = starts[j] - starts[i]
            if dt < -window:
                j0 = j + 1
                continue
            if dt > window:
                break
            w = 2.0 if sizes[j] >= 3 else 1.0
            acc += raw[j] * w
            weight += w
        labels.append(acc / weight >= 0.6)

    lead = [n for e, is_r in zip(events, labels) if not is_r for n in e]
    rhythm = [n for e, is_r in zip(events, labels) if is_r for n in e]

    smaller = min(len(lead), len(rhythm))
    if smaller < max(20, min_part_fraction * len(notes)):
        return None
    return lead, rhythm


def split_hands(
    notes: Sequence[NoteEvent],
    split_pitch: int = 60,               # middle C — the classical divide
    min_notes: int = 8,
) -> tuple[list[NoteEvent], list[NoteEvent]] | None:
    """Right/left hand split for keys, by register.

    A piano part written on one treble staff drowns its low register in
    ledger lines; a grand staff needs the notes split into two tracks.
    Returns (right, left) or None when everything lives on one side —
    then a single staff is honest."""
    right = [n for n in notes if n.pitch >= split_pitch]
    left = [n for n in notes if n.pitch < split_pitch]
    if len(right) < min_notes or len(left) < min_notes:
        return None
    return right, left
