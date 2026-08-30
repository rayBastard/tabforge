"""Read-back helper for exported .gp5 files.

One definition of "what a gp5 note means" (string number -> open value,
pitch = open value + fret, beat walk order), shared by the round-trip
tests and scripts/check_gp5.py so the two can never drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Gp5Contents:
    song: object
    track: object
    beats: list                     # every beat of every voice, in order
    note_beats: list                # only the beats carrying notes
    notes: list[tuple[float, int]]  # (time in quarter notes, midi pitch)
    impossible_beats: int           # beats with two notes on one string
    effects: dict                   # counts: hammer/vibrato/slide/bend
    hammer_violations: int          # hammer with no next note on that string


def read_gp5(source) -> Gp5Contents:
    """Parse a gp5 file (path or binary stream) into Gp5Contents.

    Times come from PyGuitarPro's read-back beat starts (ticks accumulated
    from the durations), so they reflect what notation software shows.
    """
    import guitarpro as gp

    song = gp.parse(source if not isinstance(source, str) else str(source))
    track = song.tracks[0]
    string_value = {s.number: s.value for s in track.strings}
    quarter_time = gp.Duration.quarterTime
    origin = song.measureHeaders[0].start

    beats: list = []
    note_beats: list = []
    notes: list[tuple[float, int]] = []
    impossible = 0
    effects = {"hammer": 0, "vibrato": 0, "slide": 0, "bend": 0}
    for measure in track.measures:
        for voice in measure.voices:
            for beat in voice.beats:
                beats.append(beat)
                strings = [n.string for n in beat.notes]
                if len(strings) != len(set(strings)):
                    impossible += 1
                if beat.notes:
                    note_beats.append(beat)
                for note in beat.notes:
                    if note.type == gp.NoteType.tie:
                        continue    # a held note, not a new attack
                    t = (beat.start - origin) / quarter_time
                    notes.append((t, string_value[note.string] + note.value))
                    if note.effect.hammer:
                        effects["hammer"] += 1
                    if note.effect.vibrato:
                        effects["vibrato"] += 1
                    if note.effect.slides:
                        effects["slide"] += 1
                    if note.effect.bend is not None:
                        effects["bend"] += 1

    # A hammer flag makes no sense without a following note on the same
    # string — that is the note being hammered/pulled to.
    violations = 0
    for i, beat in enumerate(note_beats):
        for note in beat.notes:
            if not note.effect.hammer:
                continue
            if not any(n.string == note.string
                       for nb in note_beats[i + 1:i + 3] for n in nb.notes):
                violations += 1
    return Gp5Contents(song=song, track=track, beats=beats,
                       note_beats=note_beats, notes=notes,
                       impossible_beats=impossible,
                       effects=effects, hammer_violations=violations)
