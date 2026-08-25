"""Tests for tempo-multiple folding. Pure math, no audio or ML deps."""
import unittest

from tabforge.audio.transcribe import collapse_tempo_candidates, fold_tempo


class TestFoldTempo(unittest.TestCase):
    def test_octaves_fold_into_range(self):
        self.assertAlmostEqual(fold_tempo(192.0), 96.0)
        self.assertAlmostEqual(fold_tempo(48.0), 96.0)
        self.assertAlmostEqual(fold_tempo(360.0), 90.0)

    def test_in_range_untouched(self):
        self.assertEqual(fold_tempo(129.2), 129.2)

    def test_bounds(self):
        self.assertEqual(fold_tempo(70.0), 70.0)
        # hi is exclusive: exactly 180 folds down
        self.assertAlmostEqual(fold_tempo(180.0), 90.0)

    def test_invalid_input(self):
        with self.assertRaises(ValueError):
            fold_tempo(0.0)
        with self.assertRaises(ValueError):
            fold_tempo(-120.0)
        # a range narrower than one octave could never terminate
        with self.assertRaises(ValueError):
            fold_tempo(120.0, lo=100.0, hi=150.0)


class TestCollapseTempoCandidates(unittest.TestCase):
    def test_octave_multiples_merge_into_one(self):
        out = collapse_tempo_candidates([96.0, 192.0, 48.0, 96.5])
        self.assertEqual(len(out), 1)
        bpm, weight = out[0]
        self.assertEqual(weight, 4)
        self.assertAlmostEqual(bpm, (96.0 + 96.0 + 96.0 + 96.5) / 4)

    def test_four_thirds_pair_stays_separate(self):
        # 96 vs 128 is the classic 4:3 beat-tracker ambiguity: both must
        # survive as hypotheses so the audio can decide between them.
        out = collapse_tempo_candidates([96.0, 128.0, 192.0, 64.0])
        self.assertEqual(len(out), 2)
        tempos = sorted(bpm for bpm, _ in out)
        self.assertAlmostEqual(tempos[0], 96.0)
        self.assertAlmostEqual(tempos[1], 128.0)

    def test_weight_ordering(self):
        out = collapse_tempo_candidates([100.0, 100.5, 133.0, 100.2])
        self.assertEqual(out[0][1], 3)
        self.assertAlmostEqual(out[0][0], (100.0 + 100.5 + 100.2) / 3)
        self.assertEqual(out[1][1], 1)

    def test_near_duplicates_within_tolerance_merge(self):
        out = collapse_tempo_candidates([129.0, 129.2, 258.4])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][1], 3)

    def test_empty_and_junk(self):
        self.assertEqual(collapse_tempo_candidates([]), [])
        self.assertEqual(collapse_tempo_candidates([0.0, -5.0]), [])


if __name__ == "__main__":
    unittest.main()
