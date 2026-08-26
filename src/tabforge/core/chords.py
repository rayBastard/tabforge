"""
Chord naming (task 58).

The harmony is COMMON property: the classifier looks at the pitches of
every instrument sounding in a beat window (a guitarist reading the
chord line over a piano ballad still wants "Am — F — C — G"), scores
them against interval templates, and names the winner — slash bass
included. Segmentation walks the beat grid with hysteresis so passing
notes don't make the chord line chatter.

Pure functions over note lists — no audio, fully unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

# interval sets relative to the root; ORDER = tie-break preference
# (plain triads before colored chords, so C E G names "C", not "Cadd9"
# with a phantom ninth)
CHORD_TEMPLATES: tuple[tuple[str, frozenset[int]], ...] = (
    ("", frozenset({0, 4, 7})),
    ("m", frozenset({0, 3, 7})),
    ("7", frozenset({0, 4, 7, 10})),
    ("maj7", frozenset({0, 4, 7, 11})),
    ("m7", frozenset({0, 3, 7, 10})),
    ("sus2", frozenset({0, 2, 7})),
    ("sus4", frozenset({0, 5, 7})),
    ("dim", frozenset({0, 3, 6})),
    ("aug", frozenset({0, 4, 8})),
    ("add9", frozenset({0, 2, 4, 7})),
    ("m add9", frozenset({0, 2, 3, 7})),
    ("5", frozenset({0, 7})),          # the metal chord
)

_SHARP = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_FLAT = ("C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B")


def pitch_name(pc: int, flats: bool = False) -> str:
    return (_FLAT if flats else _SHARP)[pc % 12]


@dataclass(slots=True)
class ChordGuess:
    root: int                   # pitch class
    suffix: str                 # "" | "m" | "7" | ...
    bass: int | None            # pitch class of a slash bass, or None
    score: float

    def name(self, flats: bool = False) -> str:
        base = pitch_name(self.root, flats) + self.suffix
        if self.bass is not None and self.bass != self.root:
            base += "/" + pitch_name(self.bass, flats)
        return base


def classify(weighted_pitches: Sequence[tuple[int, float]],
             min_score: float = 0.5) -> ChordGuess | None:
    """Best chord for one window of (midi_pitch, weight) pairs.

    Scoring per (root, template): the weight of pitch classes the
    template explains, minus half the weight it leaves unexplained,
    normalized; the root's own presence is required. Power chords
    ("5") are deliberately handicapped so a full triad outranks them
    whenever a third is actually sounding."""
    if not weighted_pitches:
        return None
    classes: dict[int, float] = {}
    lowest_pitch = None
    for pitch, w in weighted_pitches:
        classes[pitch % 12] = classes.get(pitch % 12, 0.0) + w
        if lowest_pitch is None or pitch < lowest_pitch:
            lowest_pitch = pitch
    total = sum(classes.values())
    if total <= 0 or len(classes) < 2:
        return None

    best: ChordGuess | None = None
    for root in classes:                      # a chord's root must sound
        for suffix, intervals in CHORD_TEMPLATES:
            member = {(root + i) % 12 for i in intervals}
            hit = sum(w for pc, w in classes.items() if pc in member)
            miss = total - hit
            score = (hit - 0.5 * miss) / total
            # a chord is its intervals: a template whose tones are NOT
            # all sounding must lose to one fully present (E+B alone is
            # "E5", not an E major with an imaginary third)
            present = sum(1 for pc in member if pc in classes)
            score *= present / len(member)
            # the root should carry real weight in its own chord
            score *= 0.5 + 0.5 * min(1.0, classes[root] / (total / len(classes)))
            if suffix == "5":
                score *= 0.8              # only wins when truly bare
            if best is None or score > best.score + 1e-9:
                best = ChordGuess(root, suffix, None, score)
    if best is None or best.score < min_score:
        return None
    bass_pc = lowest_pitch % 12
    member = {(best.root + i) % 12
              for i in dict(CHORD_TEMPLATES)[best.suffix]}
    if bass_pc != best.root and bass_pc in member:
        best.bass = bass_pc
    return best


@dataclass(slots=True)
class ChordSpan:
    start: float                # seconds
    end: float
    guess: ChordGuess


def track_chords(notes: Sequence, beats: Sequence[float],
                 hysteresis: float = 1.15,
                 min_score: float = 0.5) -> list[ChordSpan]:
    """Chord spans over the beat grid.

    notes: NoteEvents from ALL pitched parts pooled. Each beat window
    gathers the pitches sounding in it, weighted by sounded duration ×
    velocity. The current chord survives until a challenger beats its
    re-scored-in-this-window value by `hysteresis` — passing notes and
    one-beat ambiguities don't flip the label."""
    if len(beats) < 2 or not notes:
        return []
    spans: list[ChordSpan] = []
    current: ChordGuess | None = None
    span_start = beats[0]

    def window_pitches(a: float, b: float):
        out = []
        for n in notes:
            if getattr(n, "dead", False):
                continue
            overlap = min(n.end, b) - max(n.start, a)
            if overlap > 0.02:
                out.append((n.pitch, overlap * (n.velocity / 96.0)))
        return out

    def rescore(guess: ChordGuess, pitches) -> float:
        classes: dict[int, float] = {}
        for pitch, w in pitches:
            classes[pitch % 12] = classes.get(pitch % 12, 0.0) + w
        total = sum(classes.values())
        if total <= 0:
            return 0.0
        member = {(guess.root + i) % 12
                  for i in dict(CHORD_TEMPLATES)[guess.suffix]}
        hit = sum(w for pc, w in classes.items() if pc in member)
        return (hit - 0.5 * (total - hit)) / total

    for a, b in zip(beats[:-1], beats[1:]):
        pitches = window_pitches(a, b)
        candidate = classify(pitches, min_score=min_score)
        if current is None:
            if candidate is not None:
                current, span_start = candidate, a
            continue
        if candidate is None:
            continue                      # silence keeps the last chord
        same = (candidate.root == current.root
                and candidate.suffix == current.suffix)
        if not same and candidate.score > hysteresis * max(
                rescore(current, pitches), 1e-6):
            spans.append(ChordSpan(span_start, a, current))
            current, span_start = candidate, a
    if current is not None:
        spans.append(ChordSpan(span_start, beats[-1], current))
    return spans
