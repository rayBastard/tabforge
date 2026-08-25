"""Tests for tempo-multiple folding, the tempo sanity guard, and beat
smoothing. Mostly pure math; the smoothing tests need numpy."""
import unittest

from tabforge.audio.transcribe import (FALLBACK_BPM, collapse_tempo_candidates,
                                       fold_tempo, guard_tempo,
                                       repair_beats, smooth_beats)

try:
    import numpy as np
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False


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


class TestGuardTempo(unittest.TestCase):
    """Regression: detect_tempo could return (0.0, []) for short/silent
    clips, and 60/bpm then divided by zero inside the exports."""

    def test_zero_bpm_falls_back(self):
        bpm, beats, reliable = guard_tempo(0.0, [])
        self.assertEqual(bpm, FALLBACK_BPM)
        self.assertEqual(beats, [])
        self.assertFalse(reliable)

    def test_too_few_beats_fall_back(self):
        bpm, beats, reliable = guard_tempo(96.0, [0.0, 0.6, 1.2])
        self.assertEqual(bpm, FALLBACK_BPM)
        self.assertFalse(reliable)

    def test_out_of_range_bpm_falls_back(self):
        grid = [i * 0.2 for i in range(20)]
        for junk in (300.0, 30.0):
            bpm, _, reliable = guard_tempo(junk, grid)
            self.assertEqual(bpm, FALLBACK_BPM)
            self.assertFalse(reliable)

    def test_sane_tempo_passes_through(self):
        grid = [i * 0.625 for i in range(100)]
        bpm, beats, reliable = guard_tempo(96.0, grid)
        self.assertEqual(bpm, 96.0)
        self.assertEqual(len(beats), 100)
        self.assertTrue(reliable)


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


@unittest.skipUnless(HAVE_NUMPY, "numpy is not installed")
class TestSmoothBeats(unittest.TestCase):
    """The tracker wobbles around the true pulse; the score plays at a
    rigid BPM — un-smoothed jitter becomes 'the rhythm keeps jumping'."""

    def test_jitter_is_removed(self):
        rng = np.random.default_rng(3)
        true = np.arange(80) * 0.5
        jittered = true + rng.uniform(-0.04, 0.04, size=80)
        smoothed = np.asarray(smooth_beats(jittered.tolist()))
        raw_err = np.sqrt(np.mean((jittered - true) ** 2))
        new_err = np.sqrt(np.mean((smoothed - true) ** 2))
        self.assertLess(new_err, raw_err / 3,
                        "smoothing must cut the tracker jitter hard")
        self.assertLess(new_err, 0.012)

    def test_gradual_drift_survives(self):
        # tempo breathing 120 -> 100 BPM over a minute is REAL and must
        # stay in the grid — only the wobble around it goes
        intervals = np.linspace(0.5, 0.6, 80)
        true = np.concatenate([[0.0], np.cumsum(intervals)])
        smoothed = np.asarray(smooth_beats(true.tolist()))
        out_iv = np.diff(smoothed)
        self.assertGreater(out_iv[-1] - out_iv[0], 0.08,
                           "the drift must survive smoothing")
        self.assertLess(float(np.abs(smoothed - true).max()), 0.02)

    def test_short_grids_pass_through(self):
        self.assertEqual(smooth_beats([]), [])
        self.assertEqual(smooth_beats([0.0, 0.5, 1.0]), [0.0, 0.5, 1.0])


@unittest.skipUnless(HAVE_NUMPY, "numpy is not installed")
class TestRepairBeats(unittest.TestCase):
    """A skipped tracker beat makes one 'beat' twice as long: its slots
    double and every bar after it is off by a beat — the audible
    'rhythm breaks at barlines'. Repair fills skips and drops phantoms
    locally, WITHOUT forcing global rigidity (real tempo wanders)."""

    def test_skipped_beat_is_filled(self):
        beats = [k * 0.4 for k in range(50)]
        del beats[20]
        out = repair_beats(beats)
        self.assertEqual(len(out), 50)
        self.assertAlmostEqual(out[20], 8.0, places=6)
        iv = np.diff(out)
        self.assertLess(float(np.abs(iv - 0.4).max()), 1e-6)

    def test_phantom_beat_is_dropped(self):
        beats = [k * 0.4 for k in range(50)]
        beats.insert(11, 10 * 0.4 + 0.05)      # ghost 50 ms after beat 10
        out = repair_beats(beats)
        self.assertEqual(len(out), 50)
        self.assertLess(float(np.abs(np.diff(out) - 0.4).max()), 1e-6)

    def test_wandering_tempo_is_untouched(self):
        intervals = np.linspace(0.5, 0.7, 60)   # real ritardando
        beats = np.concatenate([[0.0], np.cumsum(intervals)]).tolist()
        out = repair_beats(beats)
        self.assertEqual(out, beats, "no glitches — nothing to repair")


if __name__ == "__main__":
    unittest.main()
