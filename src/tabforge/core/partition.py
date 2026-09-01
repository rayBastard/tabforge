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
    # 0.04 (batch-verify catch): once the density gate stopped
    # leaking low riffs into lead, a metal track's TRUE lead turned
    # out to be ~4.5% of the notes — the old 10% floor killed the
    # honest split it had just enabled
    min_part_fraction: float = 0.04,
    rhythm_threshold: float = 0.6,
    chord_weight: float = 2.0,
    register_override: int | None = None,
) -> tuple[list[NoteEvent], list[NoteEvent]] | None:
    """Returns (lead_notes, rhythm_notes), or None when the material
    doesn't really contain two parts (one side would be < min_part_fraction
    of the notes)."""
    events = group_into_events(notes, tolerance)
    if len(events) < 8:
        return None

    # TOP-NOTE PEELING (calibration session 1, Casey): two OVERLAPPING
    # guitars merge into one event when the solo note strikes within
    # the chord-gather window of a rhythm power chord — [41,48,68]
    # with a 20-semitone gap is not a chord, it is a dyad plus a solo
    # note. A top note sitting >= peel_gap above the rest of its event
    # peels off into its own single event, free to score as lead.
    peel_gap = 10
    peeled: list[list] = []
    for e in events:
        if len(e) >= 2:
            e_sorted = sorted(e, key=lambda n: n.pitch)
            if e_sorted[-1].pitch - e_sorted[-2].pitch >= peel_gap:
                peeled.append(e_sorted[:-1])
                peeled.append([e_sorted[-1]])
                continue
        peeled.append(list(e))
    events = peeled

    starts = [e[0].start for e in events]
    sizes = [len(e) for e in events]
    means = [sum(n.pitch for n in e) / len(e) for e in events]
    all_pitches = sorted(n.pitch for n in notes)
    register_pivot = all_pitches[len(all_pitches) // 2]  # median pitch
    _n = len(all_pitches)
    register_scale = max(
        4.0, (all_pitches[int(_n * 0.8)] - all_pitches[int(_n * 0.2)]) / 2)

    # Raw per-event rhythm score in [0, 1].
    raw: list[float] = []
    for i, event in enumerate(events):
        if sizes[i] >= 3:
            raw.append(1.0)
            continue
        if sizes[i] == 2:
            raw.append(0.7)
            continue
        # continuous register ramp (calibration session 1): the old
        # two-step bonus stranded chordless material — a whole track
        # drifted to one side and the split died. The ramp's scale
        # adapts to the TRACK's own spread (batch-verify catch: a
        # narrow-register metal track sat entirely inside one octave,
        # the fixed /12 ramp scored everything ~0.5 and the split
        # died again).
        lift = (means[i] - register_pivot) / register_scale * 0.4
        score = 0.5 + max(-0.4, min(0.4, -lift))
        # attack density: a fast single-note run ABOVE the texture is
        # a lick; a fast LOW run is a riff (calibration session 1 —
        # the unconditional penalty floored dense chordless material
        # entirely to lead and the split died)
        if means[i] > register_pivot:
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
            w = chord_weight if sizes[j] >= 3 else 1.0
            # register-aware smoothing (calibration session 1): a
            # neighbor influences the vote in proportion to how close
            # it sits in PITCH — after peeling, interleaved voices
            # must smooth within themselves, or the solo line is
            # dragged into the rhythm around it (and chordless
            # material all drifts to one side)
            d = (means[j] - means[i]) / 10.0
            w *= 2.718281828 ** (-d * d)
            acc += raw[j] * w
            weight += w
        if (register_override is not None and sizes[i] == 1
                and means[i] >= register_pivot + register_override):
            # a single note far above the texture IS the lead voice,
            # no matter how chordal its neighborhood (calibration
            # session 1: "самые высокие ноты — к лид гитаре")
            labels.append(False)
            continue
        labels.append(acc / weight >= rhythm_threshold)

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
