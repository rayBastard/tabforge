"""Articulation classifier tests: synthetic trajectories, pure math."""
import math
import unittest

from tabforge.core.articulation import classify_articulation
from tabforge.core.fretboard import NoteEvent


def line(a, b, n=20):
    return [a + (b - a) * i / (n - 1) for i in range(n)]


class TestClassifyArticulation(unittest.TestCase):
    def test_slide_up(self):
        self.assertEqual(classify_articulation(line(0.0, 1.0)), "slide")

    def test_slide_down(self):
        self.assertEqual(classify_articulation(line(0.0, -0.9)), "slide")

    def test_small_drift_is_not_a_slide(self):
        self.assertEqual(classify_articulation(line(0.0, 0.5)), "none")

    def test_bend_up_and_back(self):
        traj = line(0.0, 0.8, 10) + line(0.8, 0.0, 10)
        self.assertEqual(classify_articulation(traj), "bend")

    def test_bend_with_hold(self):
        traj = line(0.0, 0.6, 8) + [0.6] * 8 + line(0.6, 0.05, 8)
        self.assertEqual(classify_articulation(traj), "bend")

    def test_vibrato(self):
        traj = [0.3 * math.sin(2 * math.pi * i / 10) for i in range(40)]
        self.assertEqual(classify_articulation(traj), "vibrato")

    def test_deep_wobble_is_not_vibrato(self):
        traj = [0.9 * math.sin(2 * math.pi * i / 10) for i in range(40)]
        self.assertEqual(classify_articulation(traj), "none")

    def test_flat_and_noise_are_none(self):
        self.assertEqual(classify_articulation([0.0] * 20), "none")
        jitter = [0.02 * (-1) ** i for i in range(20)]
        self.assertEqual(classify_articulation(jitter), "none")

    def test_too_short_is_none(self):
        self.assertEqual(classify_articulation([]), "none")
        self.assertEqual(classify_articulation([0.0, 0.5, 1.0]), "none")


class TestBendsPlumbing(unittest.TestCase):
    def test_note_event_default_is_empty(self):
        n = NoteEvent(60, 0.0, 0.5)
        self.assertEqual(n.bends, [])

    def test_quantize_preserves_bends(self):
        from tabforge.core.quantize import Grid, quantize

        grid = Grid(beats=[0.0, 0.5, 1.0], subdivision=2)
        src = NoteEvent(60, 0.27, 0.23, 90, bends=[0.0, 0.4, 0.8])
        out = quantize([src], grid, strength=1.0)
        self.assertEqual(out[0].bends, [0.0, 0.4, 0.8])


if __name__ == "__main__":
    unittest.main()
