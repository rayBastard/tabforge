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


class TestGatherChords(unittest.TestCase):
    def test_rolled_chord_gathers_onto_one_onset(self):
        from tabforge.core.quantize import gather_chords
        # a C major chord rolled over 70 ms, all notes sustained
        rolled = [NoteEvent(60, 0.00, 1.0), NoteEvent(64, 0.04, 1.0),
                  NoteEvent(67, 0.07, 1.0)]
        out = gather_chords(rolled, window=0.08)
        self.assertEqual({n.start for n in out}, {0.0})
        # ends stay put: durations grow by the shift
        self.assertAlmostEqual(max(n.end for n in out), 1.07)

    def test_staccato_run_is_not_gathered(self):
        from tabforge.core.quantize import gather_chords
        # short notes: the anchor stops sounding before the next one starts
        run = [NoteEvent(60 + i, i * 0.06, 0.04) for i in range(4)]
        out = gather_chords(run, window=0.08)
        self.assertEqual(len({n.start for n in out}), 4)

    def test_fast_legato_run_is_not_chained(self):
        from tabforge.core.quantize import gather_chords
        # sustained 16ths, 99 ms apart: the second note is outside the
        # anchor window, and anchoring prevents chain-gathering
        run = [NoteEvent(60 + i, i * 0.099, 0.5) for i in range(6)]
        out = gather_chords(run, window=0.08)
        self.assertEqual(len({n.start for n in out}), 6)

    def test_two_chords_stay_separate(self):
        from tabforge.core.quantize import gather_chords
        a = [NoteEvent(60, 0.0, 0.4), NoteEvent(64, 0.05, 0.4)]
        b = [NoteEvent(65, 0.5, 0.4), NoteEvent(69, 0.55, 0.4)]
        out = gather_chords(a + b, window=0.08)
        self.assertEqual(sorted({n.start for n in out}), [0.0, 0.5])


class TestDuration(unittest.TestCase):
    def test_quarter_at_120(self):
        self.assertEqual(duration_symbol(0.5, 120), (4, False))

    def test_dotted_quarter(self):
        self.assertEqual(duration_symbol(0.75, 120), (4, True))

    def test_eighth(self):
        self.assertEqual(duration_symbol(0.25, 120), (8, False))


if __name__ == "__main__":
    unittest.main()
