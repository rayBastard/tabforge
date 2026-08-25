"""Lead/rhythm split tests. Pure note-based logic, no audio."""
import unittest

from tabforge.core.fretboard import NoteEvent
from tabforge.core.partition import split_lead_rhythm


def chord(pitches, t, dur=0.4):
    return [NoteEvent(p, t, dur) for p in pitches]


def run(pitches, t0, step=0.2, dur=0.15):
    return [NoteEvent(p, t0 + i * step, dur) for i, p in enumerate(pitches)]


class TestSplitLeadRhythm(unittest.TestCase):
    def _material(self):
        notes = []
        # rhythm: low power chords every beat for 8 bars
        for i in range(16):
            notes += chord([41, 48, 53], i * 0.5)          # F power chord
        # low passing notes inside the chord section: the neighborhood
        # must pull them into rhythm
        notes += [NoteEvent(45, 1.3, 0.2), NoteEvent(43, 3.3, 0.2)]
        # lead: a high single-note solo after the chord section
        notes += run([77, 79, 80, 82, 84, 82, 80, 79, 77, 76,
                      77, 79, 80, 82, 84, 82, 80, 79, 84, 82,
                      80, 82, 84, 86], 8.3)
        return notes

    def test_chords_go_to_rhythm_and_runs_to_lead(self):
        split = split_lead_rhythm(self._material())
        self.assertIsNotNone(split)
        lead, rhythm = split
        self.assertTrue(all(n.pitch >= 76 for n in lead),
                        "lead must be the high run only")
        chord_notes = [n for n in rhythm if n.pitch <= 53]
        self.assertEqual(len(chord_notes), 50,
                         "chords and their passing notes all in rhythm")

    def test_nothing_lost_or_duplicated(self):
        notes = self._material()
        lead, rhythm = split_lead_rhythm(notes)
        self.assertEqual(len(lead) + len(rhythm), len(notes))

    def test_single_part_material_not_split(self):
        # a plain low riff: no second part to find
        notes = run([41, 44, 46, 41, 44, 48, 46, 44] * 6, 0.0, step=0.25)
        self.assertIsNone(split_lead_rhythm(notes))

    def test_tiny_input_not_split(self):
        self.assertIsNone(split_lead_rhythm(run([60, 62, 64], 0.0)))


if __name__ == "__main__":
    unittest.main()


class TestSplitHands(unittest.TestCase):
    """A piano on one treble staff drowns its bass in ledger lines: the
    grand staff needs a register split into two tracks."""

    def test_mixed_register_splits_at_middle_c(self):
        from tabforge.core.partition import split_hands
        notes = ([NoteEvent(48 + i % 8, i * 0.25, 0.2) for i in range(20)]
                 + [NoteEvent(72 + i % 8, i * 0.25, 0.2) for i in range(20)])
        result = split_hands(notes)
        self.assertIsNotNone(result)
        right, left = result
        self.assertTrue(all(n.pitch >= 60 for n in right))
        self.assertTrue(all(n.pitch < 60 for n in left))
        self.assertEqual(len(right) + len(left), len(notes))

    def test_one_register_stays_single_staff(self):
        from tabforge.core.partition import split_hands
        notes = [NoteEvent(72 + i % 8, i * 0.25, 0.2) for i in range(30)]
        self.assertIsNone(split_hands(notes),
                          "no left hand — one staff is honest")
