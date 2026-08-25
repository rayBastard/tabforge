"""Slow end-to-end pipeline runs over the synthetic fixture corpus.
Skipped in CI (core-only install): needs basic-pitch, librosa & friends."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import basic_pitch  # noqa: F401
    import guitarpro  # noqa: F401
    import librosa  # noqa: F401
    import pretty_midi  # noqa: F401
    import soundfile  # noqa: F401
    HAVE_ML = True
except ImportError:
    HAVE_ML = False

if HAVE_ML:
    import fixtures
    from tabforge.pipeline import PipelineOptions, run_pipeline


@unittest.skipUnless(HAVE_ML, "ML dependencies are not installed")
class TestFixtureCorpus(unittest.TestCase):
    """One temp dir per test; separate=False everywhere (no demucs) except
    where stem separation itself is the behavior under test is not needed —
    the mix path exercises transcription, tempo, key, fingering, export."""

    def _run(self, fixture_fn, **opts):
        with tempfile.TemporaryDirectory() as tmp:
            wav = fixture_fn(Path(tmp))
            out = Path(tmp) / "out"
            log: list[str] = []
            results = run_pipeline(
                wav, out,
                PipelineOptions(stems=("mix",), separate=False, **opts),
                progress=lambda st, msg: log.append(f"[{st}] {msg}"))
            files_exist = {ext: p.is_file()
                           for r in results for ext, p in r.files.items()}
            # the temp dir dies with the with-block: carry the gp5 bytes out
            gp5_bytes = None
            if results and (gp5 := results[0].files.get("gp5")):
                gp5_bytes = gp5.read_bytes()
            return results, log, files_exist, gp5_bytes

    def test_short_clip_does_not_crash(self):
        results, log, files, _ = self._run(fixtures.short_clip)
        # too few beats: either the guard kicked in (warning) or a sane
        # tempo was still found — but never a crash or a 0 BPM export
        for r in results:
            self.assertGreater(r.bpm, 0)
            if "tempo: estimated poorly" not in r.warnings:
                self.assertTrue(40 <= r.bpm <= 260)

    def test_leading_silence_keeps_first_note_near_measure_one(self):
        import io

        import guitarpro as gp
        results, log, files, gp5_bytes = self._run(fixtures.leading_silence)
        self.assertEqual(len(results), 1)
        self.assertIsNotNone(gp5_bytes, "gp5 must be produced")
        song = gp.parse(io.BytesIO(gp5_bytes))
        first_note_measure = next(
            m.number for m in song.tracks[0].measures
            if any(b.notes for v in m.voices for b in v.beats))
        # 2 s of silence at ~120 BPM is 4 beats = 1 measure; anchored at
        # the first beat the notes must start in the first two measures,
        # not shifted by the lead-in
        self.assertLessEqual(first_note_measure, 2,
                             "lead-in silence shifted the measures")

    def test_drumless_track_produces_results(self):
        results, log, files, _ = self._run(fixtures.drumless)
        self.assertEqual(len(results), 1)
        self.assertGreater(results[0].note_count, 5)
        self.assertTrue(all(files.values()), f"missing files: {files}")

    def test_scale_ground_truth(self):
        results, log, files, _ = self._run(fixtures.scale_ground_truth)
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r.key, fixtures.GROUND_TRUTH_KEY)
        self.assertNotIn("tempo: estimated poorly", r.warnings)
        # accept the 120 family: the tracker may land on a 2x/0.5x octave
        folded = r.bpm
        while folded < 70:
            folded *= 2
        while folded >= 180:
            folded /= 2
        self.assertAlmostEqual(folded, fixtures.GROUND_TRUTH_BPM, delta=3.0)
        self.assertGreater(r.note_count, 10)
        self.assertTrue(all(files.values()), f"missing files: {files}")

    def test_silence_returns_empty_result_not_a_crash(self):
        results, log, files, _ = self._run(fixtures.silence)
        self.assertEqual(results, [])
        self.assertTrue(any("no notes found" in line for line in log),
                        f"expected a 'no notes found' log line, got {log}")


if __name__ == "__main__":
    unittest.main()
