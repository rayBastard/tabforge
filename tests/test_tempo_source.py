"""Tempo-source selection and stem audibility tests."""
import unittest
from pathlib import Path

from tabforge.pipeline import choose_tempo_source

try:
    import numpy as np
    import soundfile as sf
    HAVE_AUDIO = True
except ImportError:
    HAVE_AUDIO = False

MIX = Path("/mix.wav")
DRUMS = Path("/stems/drums.wav")


class TestChooseTempoSource(unittest.TestCase):
    def test_audible_drums_win(self):
        src, name = choose_tempo_source({"drums": DRUMS}, MIX,
                                        is_audible=lambda p: True)
        self.assertEqual((src, name), (DRUMS, "drums"))

    def test_missing_drums_fall_back_to_mix(self):
        src, name = choose_tempo_source({"bass": Path("/stems/bass.wav")}, MIX,
                                        is_audible=lambda p: True)
        self.assertEqual((src, name), (MIX, "mix"))

    def test_silent_drums_fall_back_to_mix(self):
        # htdemucs always writes drums.wav; residual bleed must not win.
        src, name = choose_tempo_source({"drums": DRUMS}, MIX,
                                        is_audible=lambda p: False)
        self.assertEqual((src, name), (MIX, "mix"))

    def test_audibility_checked_on_the_drums_file(self):
        seen = []
        choose_tempo_source({"drums": DRUMS}, MIX,
                            is_audible=lambda p: seen.append(p) or True)
        self.assertEqual(seen, [DRUMS])


@unittest.skipUnless(HAVE_AUDIO, "numpy/soundfile are not installed")
class TestStemIsAudible(unittest.TestCase):
    def _write(self, tmp, name, y, sr=22050):
        path = Path(tmp) / name
        sf.write(str(path), y, sr)
        return path

    def test_noise_is_audible_and_silence_is_not(self):
        import tempfile

        from tabforge.audio.transcribe import stem_is_audible

        rng = np.random.default_rng(0)
        with tempfile.TemporaryDirectory() as tmp:
            loud = self._write(tmp, "loud.wav",
                               0.1 * rng.standard_normal(22050).astype("float32"))
            quiet = self._write(tmp, "quiet.wav",
                                0.001 * rng.standard_normal(22050).astype("float32"))
            self.assertTrue(stem_is_audible(loud))
            self.assertFalse(stem_is_audible(quiet))


if __name__ == "__main__":
    unittest.main()
