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


class TestTickIndex(unittest.TestCase):
    def setUp(self):
        # a breathing grid: beat lengths 0.5, 0.6, 0.5
        self.grid = Grid(beats=[0.0, 0.5, 1.1, 1.6], subdivision=2)

    def test_inside_grid_snaps_to_real_ticks(self):
        self.assertEqual(self.grid.tick_index(0.0), 0)
        self.assertEqual(self.grid.tick_index(0.5), 2)
        self.assertEqual(self.grid.tick_index(0.82), 3)   # 1.1-beat midpoint
        self.assertEqual(self.grid.tick_index(1.6), 6)

    def test_extrapolates_past_the_end(self):
        # average tick ~0.267 s: 1.6 + 2 ticks ≈ 2.13
        self.assertEqual(self.grid.tick_index(2.13), 8)

    def test_negative_before_the_start(self):
        self.assertLess(self.grid.tick_index(-0.6), 0)


class TestDuration(unittest.TestCase):
    def test_quarter_at_120(self):
        self.assertEqual(duration_symbol(0.5, 120), (4, False))

    def test_dotted_quarter(self):
        self.assertEqual(duration_symbol(0.75, 120), (4, True))

    def test_eighth(self):
        self.assertEqual(duration_symbol(0.25, 120), (8, False))


if __name__ == "__main__":
    unittest.main()
