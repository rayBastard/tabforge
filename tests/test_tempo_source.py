"""Tempo-source selection and stem audibility tests."""
import unittest
from pathlib import Path
from unittest import mock

from tabforge import pipeline
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
    def setUp(self):
        # the crest test opens real audio; these are path-logic tests
        patcher = mock.patch.object(pipeline, "_beatworthy",
                                    return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_audible_drums_win(self):
        src, name = choose_tempo_source({"drums": DRUMS}, MIX,
                                        is_audible=lambda p: True)
        self.assertEqual((src, name), (DRUMS, "drums"))

    def test_flat_hiss_drums_fall_back_to_mix(self):
        # a phantom drums stem (bleed hiss) passes RMS but not the
        # crest test — it must not feed the beat tracker (task 56)
        with mock.patch.object(pipeline, "_beatworthy",
                               return_value=False):
            src, name = choose_tempo_source({"drums": DRUMS}, MIX,
                                            is_audible=lambda p: True)
        self.assertEqual((src, name), (MIX, "mix"))

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
class TestEnsureDecodableWav(unittest.TestCase):
    def test_wav_passes_through_untouched(self):
        import tempfile

        from tabforge.audio.transcribe import ensure_decodable_wav

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "a.wav"
            sf.write(str(src), np.zeros(1000, dtype="float32"), 22050)
            out = ensure_decodable_wav(src, Path(tmp))
            self.assertEqual(out, src)

    def test_non_wav_is_reencoded_to_wav(self):
        import tempfile

        from tabforge.audio.transcribe import ensure_decodable_wav

        rng = np.random.default_rng(3)
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "a.flac"
            stereo = 0.1 * rng.standard_normal((4410, 2)).astype("float32")
            sf.write(str(src), stereo, 22050)
            out = ensure_decodable_wav(src, Path(tmp))
            self.assertNotEqual(out, src)
            self.assertEqual(out.suffix, ".wav")
            info = sf.info(str(out))
            self.assertEqual(info.channels, 2)
            self.assertEqual(info.samplerate, 22050)


@unittest.skipUnless(HAVE_AUDIO, "numpy/soundfile are not installed")
class TestMixBacking(unittest.TestCase):
    def _stem(self, tmp, name, amp, n=4410, sr=22050):
        path = Path(tmp) / f"{name}.wav"
        sf.write(str(path), np.full((n, 2), amp, dtype="float32"), sr)
        return path

    def test_excluded_stems_stay_out_and_peaks_normalize(self):
        import tempfile

        from tabforge.audio.transcribe import mix_backing

        with tempfile.TemporaryDirectory() as tmp:
            stems = {
                "guitar": self._stem(tmp, "guitar", 0.5),
                "drums": self._stem(tmp, "drums", 0.8),
                "piano": self._stem(tmp, "piano", 0.8),
            }
            out = Path(tmp) / "backing.wav"
            got = mix_backing(stems, exclude=("guitar",), out=out)
            self.assertEqual(got, out)
            data, sr = sf.read(str(out), always_2d=True)
            peak = float(np.abs(data).max())
            # drums+piano would sum to 1.6 — must be normalized, and the
            # guitar (0.5) must NOT be in the mix
            self.assertAlmostEqual(peak, 0.99, places=2)

    def test_nothing_left_returns_none(self):
        import tempfile

        from tabforge.audio.transcribe import mix_backing

        with tempfile.TemporaryDirectory() as tmp:
            stems = {"guitar": self._stem(tmp, "guitar", 0.5)}
            self.assertIsNone(mix_backing(stems, exclude=("guitar",),
                                          out=Path(tmp) / "b.wav"))

    def test_quiet_sum_is_not_boosted(self):
        import tempfile

        from tabforge.audio.transcribe import mix_backing

        with tempfile.TemporaryDirectory() as tmp:
            stems = {"drums": self._stem(tmp, "drums", 0.2),
                     "piano": self._stem(tmp, "piano", 0.1)}
            out = Path(tmp) / "b.wav"
            mix_backing(stems, exclude=(), out=out)
            data, _ = sf.read(str(out), always_2d=True)
            self.assertAlmostEqual(float(np.abs(data).max()), 0.3, places=3)


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
