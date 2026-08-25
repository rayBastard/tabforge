"""Key detection tests. The profile correlation is pure math; the
synthesized-scale test needs numpy+librosa and is skipped without them."""
import unittest

from tabforge.audio.keydetect import Key, detect_key_from_chroma

try:
    import librosa  # noqa: F401
    import numpy as np
    HAVE_AUDIO = True
except ImportError:
    HAVE_AUDIO = False


def chroma_for(pitch_classes, weights=None):
    v = [0.0] * 12
    for i, pc in enumerate(pitch_classes):
        v[pc % 12] = weights[i] if weights else 1.0
    return v


class TestDetectKeyFromChroma(unittest.TestCase):
    def test_c_major_scale_tones(self):
        # C D E F G A B, tonic emphasized like a real track's chroma
        chroma = chroma_for([0, 2, 4, 5, 7, 9, 11],
                            weights=[6, 3, 4, 3, 5, 3, 2])
        key = detect_key_from_chroma(chroma)
        self.assertEqual(key.name, "C major")

    def test_f_minor_scale_tones(self):
        # F G Ab Bb C Db Eb with tonic and dominant emphasized
        chroma = chroma_for([5, 7, 8, 10, 0, 1, 3],
                            weights=[6, 3, 4, 3, 5, 3, 3])
        key = detect_key_from_chroma(chroma)
        self.assertEqual(key.name, "F minor")

    def test_transposition_consistency(self):
        base = chroma_for([0, 2, 4, 5, 7, 9, 11],
                          weights=[6, 3, 4, 3, 5, 3, 2])
        for shift in range(12):
            shifted = base[-shift:] + base[:-shift]
            key = detect_key_from_chroma(shifted)
            self.assertEqual(key.tonic, shift % 12)
            self.assertFalse(key.minor)

    def test_accidentals(self):
        self.assertEqual(Key(0, False, 1.0).accidentals, 0)    # C major
        self.assertEqual(Key(5, True, 1.0).accidentals, -4)    # F minor
        self.assertEqual(Key(7, False, 1.0).accidentals, 1)    # G major
        self.assertEqual(Key(4, True, 1.0).accidentals, 1)     # E minor

    def test_invalid_length(self):
        with self.assertRaises(ValueError):
            detect_key_from_chroma([1.0] * 11)


@unittest.skipUnless(HAVE_AUDIO, "librosa/numpy are not installed")
class TestSynthesizedScale(unittest.TestCase):
    def test_sine_c_major_scale(self):
        sr = 22050
        midi_scale = [60, 62, 64, 65, 67, 69, 71, 72]  # C4..C5
        tones = []
        for m in midi_scale:
            f = 440.0 * 2 ** ((m - 69) / 12)
            t = np.arange(int(sr * 0.5)) / sr
            tones.append(0.5 * np.sin(2 * np.pi * f * t))
        y = np.concatenate(tones).astype(np.float32)

        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        key = detect_key_from_chroma(np.mean(chroma, axis=1).tolist())
        self.assertEqual(key.name, "C major")


if __name__ == "__main__":
    unittest.main()
