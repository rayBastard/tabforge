import unittest

from tabforge.core.fretboard import NoteEvent
from tabforge.core.quantize import Grid, duration_symbol, quantize


class TestGrid(unittest.TestCase):
    def test_snap(self):
        grid = Grid(beats=[0.0, 0.5, 1.0], subdivision=2)  # 120 BPM, eighths
        _, t = grid.snap(0.27)
        self.assertAlmostEqual(t, 0.25)

    def test_quantize_full(self):
        grid = Grid(beats=[0.0, 0.5, 1.0], subdivision=2)
        out = quantize([NoteEvent(60, 0.27, 0.23)], grid, strength=1.0)
        self.assertAlmostEqual(out[0].start, 0.25)

    def test_quantize_half(self):
        grid = Grid(beats=[0.0, 0.5, 1.0], subdivision=2)
        out = quantize([NoteEvent(60, 0.30, 0.25)], grid, strength=0.5)
        self.assertAlmostEqual(out[0].start, 0.275)


class TestDuration(unittest.TestCase):
    def test_quarter_at_120(self):
        self.assertEqual(duration_symbol(0.5, 120), (4, False))

    def test_dotted_quarter(self):
        self.assertEqual(duration_symbol(0.75, 120), (4, True))

    def test_eighth(self):
        self.assertEqual(duration_symbol(0.25, 120), (8, False))


if __name__ == "__main__":
    unittest.main()
