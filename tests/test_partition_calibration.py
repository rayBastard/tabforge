"""Calibration-session-1 splitter mechanics (Casey, 22 flags):
top-note peeling, register ramp, register-aware smoothing."""
import unittest

from tabforge.core.fretboard import NoteEvent
from tabforge.core.partition import split_lead_rhythm


class TestOverlappingGuitars(unittest.TestCase):
    def test_solo_over_power_chords_separates(self):
        # the Casey texture in miniature: low power dyads striking
        # WITH the solo notes (same onset window) — the merged event
        # [41,48,68] used to read as a chord = unconditional rhythm
        notes = []
        for k in range(24):
            t = k * 0.3
            notes.append(NoteEvent(41, t, 0.28))
            notes.append(NoteEvent(48, t, 0.28))
            notes.append(NoteEvent(65 + (k % 4), t + 0.01, 0.2))
            notes.append(NoteEvent(67 - (k % 3), t + 0.16, 0.12))
        notes.sort(key=lambda n: n.start)
        res = split_lead_rhythm(notes)
        self.assertIsNotNone(res, "the overlap texture must split")
        lead, rhythm = res
        hi_in_lead = sum(1 for n in lead if n.pitch >= 60)
        hi_total = sum(1 for n in notes if n.pitch >= 60)
        lo_in_rhythm = sum(1 for n in rhythm if n.pitch <= 48)
        lo_total = sum(1 for n in notes if n.pitch <= 48)
        self.assertGreaterEqual(hi_in_lead / hi_total, 0.8,
                                f"solo stuck in rhythm: {hi_in_lead}/{hi_total}")
        self.assertGreaterEqual(lo_in_rhythm / lo_total, 0.9,
                                f"riff leaked to lead: {lo_in_rhythm}/{lo_total}")

    def test_fast_low_riff_is_not_a_lick(self):
        # dense LOW sixteenths = riff (rhythm); the old unconditional
        # density penalty pushed them lead and killed chordless splits
        notes = []
        for k in range(64):
            notes.append(NoteEvent(40 + (k % 2) * 3, k * 0.14, 0.12))
        for k in range(16):
            notes.append(NoteEvent(70 + (k % 5), 2.0 + k * 0.14, 0.1))
        notes.sort(key=lambda n: n.start)
        res = split_lead_rhythm(notes)
        if res is None:
            return                        # not splitting is acceptable
        lead, _rhythm = res
        low_in_lead = sum(1 for n in lead if n.pitch <= 45)
        self.assertLessEqual(low_in_lead, 6,
                             "the low riff drifted into lead")


if __name__ == "__main__":
    unittest.main()
