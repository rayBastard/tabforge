"""Harmonic leak validation: a note must live where its energy lives."""
import tempfile
import unittest
from pathlib import Path

from tabforge.core.fretboard import NoteEvent

try:
    import numpy as np
    import soundfile as sf
    HAVE_AUDIO = True
except ImportError:
    HAVE_AUDIO = False

SR = 22050


@unittest.skipUnless(HAVE_AUDIO, "numpy/soundfile are not installed")
class TestLeakFilter(unittest.TestCase):
    def _stems(self, tmp: Path):
        t = np.arange(SR * 2) / SR
        tone = np.zeros(SR * 2)
        f = 440.0 * 2 ** ((57 - 69) / 12)          # A3
        seg = np.sin(2 * np.pi * f * t[: SR]) \
            + 0.5 * np.sin(2 * np.pi * 2 * f * t[: SR])
        tone[:SR] = seg * 0.8
        quiet = np.zeros(SR * 2)
        paths = {}
        for name, y in (("guitar", tone), ("other", quiet),
                        ("bass", quiet), ("piano", quiet),
                        ("vocals", quiet)):
            p = tmp / f"{name}.wav"
            sf.write(str(p), y, SR)
            paths[name] = p
        return paths

    def test_note_where_the_energy_is_survives(self):
        from tabforge.audio.validate import filter_leaked_notes
        with tempfile.TemporaryDirectory() as tmp:
            stems = self._stems(Path(tmp))
            notes = [NoteEvent(57, 0.1, 0.5)]
            kept = filter_leaked_notes(notes, "guitar", stems)
            self.assertEqual(len(kept), 1)

    def test_echo_of_another_stem_is_dropped(self):
        from tabforge.audio.validate import filter_leaked_notes
        with tempfile.TemporaryDirectory() as tmp:
            stems = self._stems(Path(tmp))
            # the same A3 claimed by "other", whose audio is silence:
            # the energy plainly lives in the guitar stem
            notes = [NoteEvent(57, 0.1, 0.5)]
            kept = filter_leaked_notes(notes, "other", stems)
            self.assertEqual(kept, [])

    def test_zero_margin_disables_nothing_here(self):
        # margin guards live in the pipeline (leak_margin=0 skips the
        # filter); the function itself always filters when called
        from tabforge.audio.validate import filter_leaked_notes
        with tempfile.TemporaryDirectory() as tmp:
            stems = self._stems(Path(tmp))
            notes = [NoteEvent(57, 1.5, 0.4)]   # silence everywhere
            kept = filter_leaked_notes(notes, "guitar", stems)
            self.assertEqual(len(kept), 1, "ties keep the note")


if __name__ == "__main__":
    unittest.main()
