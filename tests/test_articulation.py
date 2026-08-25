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


class TestDetectLegatoPairs(unittest.TestCase):
    @staticmethod
    def pair(p1, p2, gap=0.02, v1=100, v2=70):
        first = NoteEvent(p1, 0.0, 0.28, v1)
        return [first, NoteEvent(p2, first.end + gap, 0.3, v2)]

    def test_ascending_pair_is_hammer_on(self):
        from tabforge.core.articulation import detect_legato_pairs
        pairs = detect_legato_pairs(self.pair(55, 59))
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0][2], "hammer-on")

    def test_descending_pair_is_pull_off(self):
        from tabforge.core.articulation import detect_legato_pairs
        pairs = detect_legato_pairs(self.pair(59, 55))
        self.assertEqual(pairs[0][2], "pull-off")

    def test_small_overlap_still_qualifies(self):
        from tabforge.core.articulation import detect_legato_pairs
        self.assertEqual(len(detect_legato_pairs(self.pair(55, 57, gap=-0.02))), 1)

    def test_rejections(self):
        from tabforge.core.articulation import detect_legato_pairs
        # too far apart in time
        self.assertEqual(detect_legato_pairs(self.pair(55, 57, gap=0.2)), [])
        # interval too wide
        self.assertEqual(detect_legato_pairs(self.pair(55, 60)), [])
        # second note louder = picked, not hammered
        self.assertEqual(detect_legato_pairs(self.pair(55, 57, v2=110)), [])
        # chords are excluded
        notes = self.pair(55, 57)
        notes.append(NoteEvent(48, 0.0, 0.28, 100))
        self.assertEqual(detect_legato_pairs(notes), [])


class TestLegatoFingering(unittest.TestCase):
    def _strings(self, notes, legato):
        from tabforge.core.fretboard import assign_tab
        shapes = assign_tab(notes, legato=legato)
        return [(s.placements[0].string, s.placements[0].fret) for s in shapes]

    def test_legato_pair_lands_on_one_string(self):
        from tabforge.core.articulation import detect_legato_pairs
        # G3 -> B3: without the flag the cheapest layout is two open
        # strings; the legato flag must pull it onto one string.
        notes = [NoteEvent(55, 0.0, 0.28, 100), NoteEvent(59, 0.3, 0.3, 70)]
        plain = self._strings(notes, None)
        self.assertNotEqual(plain[0][0], plain[1][0],
                            "baseline must use two strings for the test "
                            "to be meaningful")
        pairs = detect_legato_pairs(notes)
        self.assertEqual(len(pairs), 1)
        legato = self._strings(notes, pairs)
        self.assertEqual(legato[0][0], legato[1][0],
                         "legato pair must land on one string")

    def test_legato_is_not_forced(self):
        from tabforge.core.articulation import detect_legato_pairs
        from tabforge.core.fretboard import TabConfig
        # D4 -> F#4 keeps its two-string layout even with the flag (the
        # bonus loses to the position costs) — and that is fine: the pair
        # simply stays ordinary notes with the right pitches.
        notes = [NoteEvent(62, 0.0, 0.28, 100), NoteEvent(66, 0.3, 0.3, 70)]
        layout = self._strings(notes, detect_legato_pairs(notes))
        cfg = TabConfig()
        got = sorted(cfg.tuning[s] + f for s, f in layout)
        self.assertEqual(got, [62, 66])


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
