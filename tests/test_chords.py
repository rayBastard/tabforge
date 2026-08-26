"""Chord classifier + segmentation (task 58): synthetic harmony."""
import unittest

from tabforge.core.chords import ChordGuess, classify, track_chords
from tabforge.core.fretboard import NoteEvent


def _w(*pitches):
    return [(p, 1.0) for p in pitches]


class TestClassify(unittest.TestCase):
    def test_major_minor(self):
        self.assertEqual(classify(_w(60, 64, 67)).name(), "C")
        self.assertEqual(classify(_w(57, 60, 64)).name(), "Am")

    def test_sevenths(self):
        self.assertEqual(classify(_w(55, 59, 62, 65)).name(), "G7")
        self.assertEqual(classify(_w(60, 64, 67, 71)).name(), "Cmaj7")
        self.assertEqual(classify(_w(62, 65, 69, 72)).name(), "Dm7")

    def test_sus_dim_aug(self):
        self.assertEqual(classify(_w(62, 64, 69)).name(), "Dsus2")
        self.assertEqual(classify(_w(64, 69, 71)).name(), "Esus4")
        self.assertEqual(classify(_w(59, 62, 65)).name(), "Bdim")
        self.assertEqual(classify(_w(60, 64, 68)).name(), "Caug")

    def test_power_chord_and_triad_priority(self):
        # bare fifth names "5"; with a third present the triad must win
        self.assertEqual(classify(_w(40, 47, 52)).name(), "E5")
        self.assertEqual(classify(_w(40, 44, 47, 52)).name(), "E")

    def test_slash_bass(self):
        # C major over an E bass = C/E
        self.assertEqual(classify(_w(52, 60, 64, 67)).name(), "C/E")

    def test_flats_spelling(self):
        g = classify(_w(58, 62, 65))
        self.assertEqual(g.name(flats=True), "Bb")
        self.assertEqual(g.name(flats=False), "A#")

    def test_garbage_returns_none(self):
        self.assertIsNone(classify([]))
        self.assertIsNone(classify(_w(60)))          # a lone note
        # a chromatic cluster fits nothing well
        self.assertIsNone(classify(_w(60, 61, 62, 63, 64, 65, 66)))


class TestTrackChords(unittest.TestCase):
    def _notes(self, spec):
        """spec: list of (pitch, start, dur)."""
        return [NoteEvent(p, s, d) for p, s, d in spec]

    def test_progression_segments(self):
        beats = [float(b) for b in range(9)]
        notes = []
        for beat, chord in ((0, (57, 60, 64)), (4, (53, 57, 60))):
            for p in chord:
                notes.append(NoteEvent(p, float(beat), 4.0))
        spans = track_chords(notes, beats)
        self.assertEqual([s.guess.name() for s in spans], ["Am", "F"])
        self.assertEqual([round(s.start) for s in spans], [0, 4])

    def test_passing_note_does_not_flip(self):
        beats = [float(b) for b in range(5)]
        notes = [NoteEvent(p, 0.0, 4.0) for p in (57, 60, 64)]  # Am held
        notes.append(NoteEvent(62, 2.0, 0.4))    # passing D on beat 3
        spans = track_chords(notes, beats)
        self.assertEqual([s.guess.name() for s in spans], ["Am"])

    def test_silence_keeps_the_last_chord(self):
        beats = [float(b) for b in range(7)]
        notes = [NoteEvent(p, 0.0, 2.0) for p in (57, 60, 64)]
        notes += [NoteEvent(p, 4.0, 2.0) for p in (55, 59, 62)]
        spans = track_chords(notes, beats)
        self.assertEqual([s.guess.name() for s in spans], ["Am", "G"])

    def test_dead_notes_ignored(self):
        beats = [float(b) for b in range(3)]
        notes = [NoteEvent(p, 0.0, 2.0) for p in (57, 60, 64)]
        notes.append(NoteEvent(30, 0.0, 2.0, dead=True))
        spans = track_chords(notes, beats)
        self.assertEqual(spans[0].guess.name(), "Am")


if __name__ == "__main__":
    unittest.main()


class TestGp5ChordLabels(unittest.TestCase):
    def test_labels_land_on_beats(self):
        import tempfile
        from pathlib import Path

        import guitarpro as gp

        from tabforge.core import TabConfig, TUNINGS
        from tabforge.core.fretboard import assign_tab
        from tabforge.core.instruments import profile_for
        from tabforge.export.writers import SongPart, export_song_gp5

        cfg = TabConfig()
        notes = [NoteEvent(52 + i, i * 0.5, 0.4) for i in range(8)]
        part = SongPart("guitar", assign_tab(notes, cfg), cfg,
                        profile_for("guitar"))
        chords = [(0, "Em", [0, 2, 2, 0, 0, 0]),
                  (960 * 2, "C", [-1, 3, 2, 0, 1, 0])]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "song.gp5"
            export_song_gp5([part], path, bpm=120.0, chords=chords)
            song = gp.parse(str(path))
        names = [b.effect.chord.name
                 for m in song.tracks[0].measures
                 for v in m.voices for b in v.beats
                 if b.effect.chord is not None]
        self.assertEqual(names, ["Em", "C"])
