"""Drum transcription tests. The shape/grid half is core-only; the
classifier half needs numpy/librosa/soundfile and is skipped in the
core CI, matching the other audio suites."""
import tempfile
import unittest
from pathlib import Path

from tabforge.audio.drums import (CRASH, HIHAT, HIHAT_OPEN, KICK, RIDE,
                                  SNARE, TOM, TOM_FLOOR, TOM_HIGH,
                                  classify_hit, drum_shapes,
                                  render_drum_ascii, transcribe_drums)
from tabforge.core.fretboard import NoteEvent

try:
    import librosa  # noqa: F401
    import numpy as np
    import soundfile as sf
    HAVE_AUDIO = True
except ImportError:
    HAVE_AUDIO = False

SR = 22050


class TestDrumShapes(unittest.TestCase):
    def test_simultaneous_hits_share_a_beat(self):
        hits = [NoteEvent(KICK, 0.0, 0.1), NoteEvent(HIHAT, 0.01, 0.1),
                NoteEvent(SNARE, 0.5, 0.1)]
        shapes = drum_shapes(hits)
        self.assertEqual(len(shapes), 2)
        first = shapes[0].placements
        self.assertEqual(sorted(p.fret for p in first), [KICK, HIHAT])
        # gp5 needs one string per voice within a beat
        strings = [p.string for p in first]
        self.assertEqual(len(strings), len(set(strings)))
        for shape in shapes:
            for p in shape.placements:
                self.assertEqual(p.fret, p.note.pitch,
                                 "the GM number rides in the fret field")

    def test_duplicate_voice_keeps_the_louder_hit(self):
        hits = [NoteEvent(KICK, 0.0, 0.1, velocity=70),
                NoteEvent(KICK, 0.01, 0.1, velocity=110)]
        shapes = drum_shapes(hits)
        self.assertEqual(len(shapes), 1)
        self.assertEqual(len(shapes[0].placements), 1)
        self.assertEqual(shapes[0].placements[0].note.velocity, 110)


class TestDrumAscii(unittest.TestCase):
    def test_grid_lines(self):
        shapes = drum_shapes([NoteEvent(KICK, 0.0, 0.1),
                              NoteEvent(HIHAT, 0.01, 0.1),
                              NoteEvent(SNARE, 0.5, 0.1)])
        lines = render_drum_ascii(shapes).splitlines()
        self.assertEqual([ln[0] for ln in lines], list("CRHTSK"))
        grid = {ln[0]: ln for ln in lines}
        self.assertEqual(grid["K"], "K|x-|")
        self.assertEqual(grid["H"], "H|x-|")
        self.assertEqual(grid["S"], "S|-x|")
        self.assertEqual(grid["C"], "C|--|")

    def test_empty(self):
        self.assertEqual(render_drum_ascii([]), "")


def _tone(freq: float, dur: float, tau: float, partials=((1, 1.0),)):
    t = np.arange(int(dur * SR)) / SR
    y = sum(a * np.sin(2 * np.pi * freq * k * t) for k, a in partials)
    return y * np.exp(-t / tau)


def _band_noise(rng, dur: float, lo: float, hi: float, tau: float):
    """Noise confined to one band, with an exponential decay."""
    n = int(dur * SR)
    freqs = np.fft.rfftfreq(n, 1.0 / SR)
    spec = np.zeros(len(freqs), dtype=complex)
    sel = (freqs >= lo) & (freqs < hi)
    spec[sel] = np.exp(2j * np.pi * rng.random(int(sel.sum())))
    y = np.fft.irfft(spec, n)
    y /= np.abs(y).max() or 1.0
    t = np.arange(n) / SR
    return y * np.exp(-t / tau)


@unittest.skipUnless(HAVE_AUDIO, "numpy/librosa are not installed")
class TestClassify(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(7)

    def test_kick_is_low(self):
        self.assertEqual(classify_hit(_tone(55, 0.3, 0.1), SR), KICK)

    def test_toms_split_by_pitch(self):
        harm = ((1, 1.0), (2, 0.4))
        self.assertEqual(
            classify_hit(_tone(95, 0.3, 0.25, harm), SR), TOM_FLOOR)
        self.assertEqual(
            classify_hit(_tone(140, 0.3, 0.2, harm), SR), TOM)
        self.assertEqual(
            classify_hit(_tone(210, 0.3, 0.18, harm), SR), TOM_HIGH)

    def test_snare_is_noisy_mid(self):
        seg = _band_noise(self.rng, 0.3, 150, 2000, tau=0.08)
        self.assertEqual(classify_hit(seg, SR), SNARE)

    def test_hihat_open_and_closed_split_by_decay(self):
        closed = _band_noise(self.rng, 0.3, 5000, 9000, tau=0.02)
        self.assertEqual(classify_hit(closed, SR), HIHAT)
        opened = _band_noise(self.rng, 0.3, 5000, 9000, tau=0.15)
        self.assertEqual(classify_hit(opened, SR), HIHAT_OPEN)

    def test_crash_is_ringing_noise_ride_is_ringing_ping(self):
        crash = _band_noise(self.rng, 0.3, 4000, 10000, tau=0.4)
        self.assertEqual(classify_hit(crash, SR), CRASH)
        ride = _tone(5200, 0.3, 0.5, ((1, 1.0), (1.48, 0.6), (2.1, 0.3)))
        self.assertEqual(classify_hit(ride, SR), RIDE)


@unittest.skipUnless(HAVE_AUDIO, "numpy/librosa/soundfile are not installed")
class TestTranscribe(unittest.TestCase):
    def test_synthetic_kit(self):
        rng = np.random.default_rng(7)
        expected = [(0.5, KICK), (1.0, SNARE), (1.5, HIHAT), (2.0, CRASH)]
        y = np.zeros(SR * 3)
        for t, gm in expected:
            if gm == KICK:
                hit = _tone(55, 0.3, 0.1)
            elif gm == SNARE:
                hit = _band_noise(rng, 0.3, 150, 2000, tau=0.08)
            elif gm == HIHAT:
                hit = _band_noise(rng, 0.3, 5000, 9000, tau=0.02)
            else:
                hit = _band_noise(rng, 0.3, 5000, 9000, tau=0.4)
            i = int(t * SR)
            y[i: i + len(hit)] += hit
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "drums.wav"
            sf.write(str(wav), y, SR)
            hits = transcribe_drums(wav)
        for t, gm in expected:
            match = [h for h in hits if abs(h.start - t) < 0.06]
            self.assertTrue(match, f"no onset detected near {t}s")
            self.assertIn(gm, [h.pitch for h in match],
                          f"the hit at {t}s must classify as {gm}")
        for h in hits:
            self.assertGreaterEqual(h.velocity, 60)


if __name__ == "__main__":
    unittest.main()
