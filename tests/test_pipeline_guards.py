"""A broken key detector must degrade the job, not kill it."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tabforge.core.fretboard import NoteEvent
from tabforge import pipeline


class TestSuggestTuning(unittest.TestCase):
    def test_guitar(self):
        self.assertEqual(pipeline.suggest_tuning("guitar", 40), "standard")
        self.assertEqual(pipeline.suggest_tuning("guitar", 45), "standard")
        self.assertEqual(pipeline.suggest_tuning("guitar", 39), "eb_standard")
        self.assertEqual(pipeline.suggest_tuning("guitar", 38), "drop_d")
        self.assertEqual(pipeline.suggest_tuning("guitar", 35), "drop_d")

    def test_bass(self):
        self.assertEqual(pipeline.suggest_tuning("bass", 28), "bass_4")
        self.assertEqual(pipeline.suggest_tuning("bass", 26), "bass_5")

    def test_no_pitch_or_unpitched_stem(self):
        self.assertIsNone(pipeline.suggest_tuning("guitar", None))
        self.assertIsNone(pipeline.suggest_tuning("piano", 30))
        self.assertIsNone(pipeline.suggest_tuning("vocals", 50))


class TestKeyDetectionGuard(unittest.TestCase):
    def _run(self, detect_key):
        notes = [NoteEvent(60 + i, i * 0.5, 0.4) for i in range(4)]
        patches = {
            "transcribe_stem": mock.Mock(return_value=notes),
            "detect_tempo": mock.Mock(return_value=(120.0, [], True)),
            "load_audio": mock.Mock(return_value=(None, 22050)),
        }
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.multiple("tabforge.audio.transcribe", **patches), \
             mock.patch("tabforge.audio.keydetect.detect_key", detect_key), \
             mock.patch.multiple("tabforge.export.writers",
                                 export_midi=mock.DEFAULT,
                                 export_ascii=mock.DEFAULT,
                                 export_gp5=mock.DEFAULT,
                                 export_musicxml=mock.DEFAULT):
            opts = pipeline.PipelineOptions(stems=("mix",), separate=False)
            return pipeline.run_pipeline(Path("song.wav"), Path(tmp), opts)

    def test_key_detector_failure_does_not_kill_the_job(self):
        results = self._run(mock.Mock(side_effect=RuntimeError("no decoder")))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].key, "unknown key")
        self.assertIn("key: detection failed", results[0].warnings)

    def test_working_detector_still_reports_the_key(self):
        from tabforge.audio.keydetect import Key
        results = self._run(mock.Mock(return_value=Key(5, True, 0.8)))
        self.assertEqual(results[0].key, "F minor")
        self.assertNotIn("key: detection failed", results[0].warnings)


if __name__ == "__main__":
    unittest.main()
