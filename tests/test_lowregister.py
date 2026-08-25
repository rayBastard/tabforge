"""Octave double-pass merge logic (the passes themselves are mocked)."""
import unittest
from unittest import mock

from tabforge.core.fretboard import NoteEvent


class TestLowRegisterMerge(unittest.TestCase):
    def _merge(self, normal, low):
        from tabforge.audio import lowregister as lr
        with mock.patch.object(lr, "_octave_pass", return_value=low), \
             mock.patch("tabforge.audio.transcribe.transcribe_stem",
                        return_value=normal):
            return lr.transcribe_with_low_pass("x.wav", {})

    def test_zones(self):
        normal = [NoteEvent(60, 0.0, 0.5), NoteEvent(40, 1.0, 0.5)]
        low = [NoteEvent(40, 1.02, 0.5), NoteEvent(30, 2.0, 0.5),
               NoteEvent(60, 0.0, 0.5)]
        out = self._merge(normal, low)
        pitches = sorted((n.pitch, round(n.start, 2)) for n in out)
        # 60 from normal (top), 40 from the octave pass (bottom),
        # 30 from the octave pass, high copy of 60 deduped
        self.assertEqual(pitches, [(30, 2.0), (40, 1.02), (60, 0.0)])

    def test_crossover_prefers_louder(self):
        normal = [NoteEvent(46, 0.0, 0.5, velocity=50)]
        low = [NoteEvent(46, 0.01, 0.5, velocity=90)]
        out = self._merge(normal, low)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].velocity, 90)


if __name__ == "__main__":
    unittest.main()
