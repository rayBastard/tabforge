"""Task 74: meter changes within a track."""
import unittest

from tabforge.pipeline import AnalyzeResult, _measure_meters, meter_segments


def _windows(votes, t0=0.0, win=20.0, hop=5.0):
    return [[t0 + i * hop, t0 + i * hop + win, v]
            for i, v in enumerate(votes)]


class TestMeterSegments(unittest.TestCase):
    def test_uniform_song_reports_no_changes(self):
        self.assertEqual(meter_segments(_windows([4] * 20)), [])
        self.assertEqual(meter_segments(_windows([3] * 20)), [])

    def test_single_change_detected(self):
        # 12 windows of 4/4 then 9 of 3/4 (the spliced-fixture shape)
        segs = meter_segments(_windows([4] * 12 + [3] * 9))
        self.assertEqual(len(segs), 1)
        t, m = segs[0]
        self.assertEqual(m, 3)
        # boundary window centers: last 4-window and first 3-window
        self.assertAlmostEqual(t, (55 + 20 / 2 + 60 + 20 / 2) / 2,
                               delta=2.6)

    def test_short_blip_is_not_a_change(self):
        # two stray votes cannot rewrite the song's meter
        self.assertEqual(meter_segments(_windows([4] * 10 + [3] * 2
                                                 + [4] * 10)), [])

    def test_zero_votes_ignored(self):
        self.assertEqual(meter_segments(_windows([0] * 6 + [4] * 8)), [])


class TestMeasureMeters(unittest.TestCase):
    def _analyzed(self, changes, meter=4, n_beats=40, step=0.5):
        return AnalyzeResult(stems={}, analysis={}, bpm=60 / step,
                             beats=[i * step for i in range(n_beats)],
                             tempo_reliable=True, key=None, meter=meter,
                             meter_changes=changes)

    def test_uniform_returns_none(self):
        self.assertIsNone(_measure_meters(self._analyzed([])))

    def test_change_snaps_to_barline(self):
        # beats at 0.5 s; 4/4 bars are 2 s; change reported at 10.3 s
        # -> bars 0-4 in 4/4, 3/4 from bar 5 (starting beat 20 = 10 s)
        meters = _measure_meters(self._analyzed([(10.3, 3)]))
        self.assertEqual(meters[:5], [4] * 5)
        self.assertTrue(all(m == 3 for m in meters[5:]))

    def test_meters_cover_all_beats(self):
        meters = _measure_meters(self._analyzed([(10.0, 3)]))
        self.assertEqual(sum(meters) >= 40, True)


if __name__ == "__main__":
    unittest.main()
