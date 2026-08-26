"""Section detection (task 59): synthetic structure."""
import tempfile
import unittest
from pathlib import Path

import numpy as np

from tabforge.audio.sections import detect_sections


def _features(path, pattern, beats_per_block=32):
    """pattern: e.g. 'AABA' — each letter a chroma block."""
    profiles = {"A": np.eye(12)[:, [0, 4, 7]].sum(axis=1),
                "B": np.eye(12)[:, [2, 5, 9]].sum(axis=1),
                "C": np.eye(12)[:, [1, 6, 10]].sum(axis=1)}
    louds = {"A": 0.2, "B": 0.6, "C": 0.3}
    cols, rms = [], []
    rng = np.random.default_rng(3)
    for ch in pattern:
        base = profiles[ch]
        for _ in range(beats_per_block):
            cols.append(base + rng.normal(0, 0.02, 12))
            rms.append(louds[ch] + rng.normal(0, 0.01))
    np.savez(str(path), chroma=np.array(cols).T, rms=np.array(rms))
    return len(cols)


class TestDetectSections(unittest.TestCase):
    def test_boundaries_and_labels(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "feat.npz"
            n = _features(f, "ABAB")
            beats = [float(i) for i in range(n + 1)]
            secs = detect_sections(f, beats, 4)
        self.assertEqual(len(secs), 4)
        starts = [round(s["start"]) for s in secs]
        self.assertEqual(starts, [0, 32, 64, 96])
        # B repeats and is the loud one -> Chorus; A -> Verse
        self.assertEqual([s["label"] for s in secs],
                         ["Verse", "Chorus", "Verse", "Chorus"])

    def test_harmony_vote_sharpens_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "feat.npz"
            n = _features(f, "AAB")
            beats = [float(i) for i in range(n + 1)]
            chords = [{"start": 0.0, "name": "C"},
                      {"start": 64.0, "name": "Dm"}]
            secs = detect_sections(f, beats, 4, chords)
        self.assertIn(64, [round(s["start"]) for s in secs])

    def test_no_features_is_empty(self):
        secs = detect_sections(Path("/nonexistent.npz"),
                               [float(i) for i in range(64)], 4)
        self.assertEqual(secs, [])


if __name__ == "__main__":
    unittest.main()
