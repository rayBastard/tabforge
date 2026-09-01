"""Bend rescue (calibration case #4): missed sustained notes come
back from the stem with their contours; junk stays out."""
import tempfile
import unittest
from pathlib import Path

import numpy as np

from tabforge.core.fretboard import NoteEvent

SR = 22050


def _tone(midi, dur, glide=0.0):
    f0 = 440 * 2 ** ((midi - 69) / 12)
    t = np.arange(int(dur * SR)) / SR
    f = f0 * 2 ** (glide * (t / dur) / 12)
    ph = 2 * np.pi * np.cumsum(f) / SR
    return 0.4 * np.sin(ph) * np.minimum(1, t / 0.01)


class TestRescue(unittest.TestCase):
    def _wav(self, y):
        import soundfile as sf
        td = tempfile.mkdtemp()
        p = Path(td) / "g.wav"
        sf.write(str(p), y, SR)
        return p

    def test_missing_bent_note_recovered(self):
        from tabforge.audio.bendrescue import rescue_missing_notes
        y = np.zeros(int(4 * SR))
        s = _tone(76, 1.0, glide=1.0)          # a whole-step bend up
        y[SR:SR + len(s)] += s
        notes = [NoteEvent(52, 0.0, 0.4), NoteEvent(52, 3.0, 0.4)]
        added = rescue_missing_notes(notes, self._wav(y))
        self.assertEqual(added, 1)
        rec = [n for n in notes if n.pitch >= 70][0]
        self.assertTrue(rec.bends, "the bend contour must come along")
        self.assertGreaterEqual(max(rec.bends), 0.6)

    def test_transcribed_note_not_duplicated(self):
        from tabforge.audio.bendrescue import rescue_missing_notes
        y = np.zeros(int(3 * SR))
        s = _tone(76, 1.0)
        y[SR:SR + len(s)] += s
        notes = [NoteEvent(76, 1.0, 1.0)]      # already transcribed
        self.assertEqual(rescue_missing_notes(notes, self._wav(y)), 0)

    def test_existing_note_gains_its_bend(self):
        from tabforge.audio.bendrescue import annotate_bends
        y = np.zeros(int(3 * SR))
        s = _tone(76, 1.0, glide=1.0)
        y[SR:SR + len(s)] += s
        notes = [NoteEvent(76, 1.0, 1.0)]
        self.assertEqual(annotate_bends(notes, self._wav(y)), 1)
        self.assertGreaterEqual(max(notes[0].bends), 0.6)

    def test_flat_note_stays_flat(self):
        from tabforge.audio.bendrescue import annotate_bends
        y = np.zeros(int(3 * SR))
        s = _tone(76, 1.0)
        y[SR:SR + len(s)] += s
        notes = [NoteEvent(76, 1.0, 1.0)]
        self.assertEqual(annotate_bends(notes, self._wav(y)), 0)
        self.assertEqual(notes[0].bends, [])


if __name__ == "__main__":
    unittest.main()
