"""Palm-mute detection on Karplus-Strong synthesis, where damping is
the ground truth, plus the gp5 P.M. round-trip."""
import tempfile
import unittest
from pathlib import Path

import numpy as np

from tabforge.core.fretboard import NoteEvent

try:
    import guitarpro as gp
    HAVE_GP = True
except ImportError:
    HAVE_GP = False

SR = 22050


def _ks(pitch: int, dur: float, muted: bool) -> np.ndarray:
    """Karplus-Strong pluck; muted = heavy loop damping + dull burst."""
    rng = np.random.default_rng(pitch)
    f0 = 440.0 * 2 ** ((pitch - 69) / 12)
    period = max(2, int(SR / f0))
    burst = rng.uniform(-1, 1, period)
    if muted:
        # a palm rests on the string: the burst starts dull too
        for _ in range(4):
            burst = np.convolve(burst, [0.25, 0.5, 0.25], mode="same")
    damp = 0.82 if muted else 0.999
    n = int(dur * SR)
    out = np.zeros(n)
    buf = burst.copy()
    for i in range(n):
        j = i % period
        out[i] = buf[j]
        buf[j] = damp * 0.5 * (buf[j] + buf[(j + 1) % period])
    return out * 0.5


def _render(notes, muted_flags) -> np.ndarray:
    total = int(max(n.end for n in notes) * SR) + SR // 2
    y = np.zeros(total)
    for n, m in zip(notes, muted_flags):
        s = _ks(n.pitch, min(n.duration + 0.3, 1.0), m)
        i0 = int(n.start * SR)
        y[i0:i0 + len(s)] += s[:max(0, total - i0)]
    return y


class TestPalmMuteDetector(unittest.TestCase):
    def _notes(self):
        notes, flags = [], []
        # 8 ringing notes, then 8 chugs, then 8 ringing again
        for k in range(8):
            notes.append(NoteEvent(45, k * 0.35, 0.3))
            flags.append(False)
        for k in range(8):
            notes.append(NoteEvent(45, 2.8 + k * 0.25, 0.2))
            flags.append(True)
        for k in range(8):
            notes.append(NoteEvent(45, 4.8 + k * 0.35, 0.3))
            flags.append(False)
        return notes, flags

    def test_chug_run_detected_ringing_untouched(self):
        import soundfile as sf

        from tabforge.audio.palmmute import detect_palm_mutes
        notes, flags = self._notes()
        y = _render(notes, flags)
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "g.wav"
            sf.write(str(wav), y, SR)
            marked = detect_palm_mutes(notes, wav)
        got = [n.palm_mute for n in notes]
        # every chug after the first marked (the first one's decay
        # window is polluted by the previous ringing note's tail —
        # honest physics), and NO ringing note marked
        self.assertEqual(got[9:16], [True] * 7, got)
        self.assertEqual(got[:8], [False] * 8, got)
        self.assertEqual(got[16:], [False] * 8, got)
        self.assertGreaterEqual(marked, 7)

    def test_lone_dull_note_is_not_marked(self):
        import soundfile as sf

        from tabforge.audio.palmmute import detect_palm_mutes
        notes = [NoteEvent(45, k * 0.4, 0.3) for k in range(9)]
        flags = [False] * 4 + [True] + [False] * 4   # one dull speckle
        y = _render(notes, flags)
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "g.wav"
            sf.write(str(wav), y, SR)
            detect_palm_mutes(notes, wav)
        self.assertTrue(all(not n.palm_mute for n in notes))


class TestHarmonicDetector(unittest.TestCase):
    def test_flageolet_detected_pluck_untouched(self):
        import soundfile as sf

        from tabforge.audio.palmmute import detect_techniques
        # plucked notes (KS), then two chimed harmonics: nearly pure
        # slowly-decaying tones an octave up
        notes, flags = [], []
        for k in range(6):
            notes.append(NoteEvent(45, k * 0.4, 0.3))
            flags.append("pluck")
        for k in range(2):
            notes.append(NoteEvent(57, 2.6 + k * 0.8, 0.6))
            flags.append("harm")
        total = int(5.0 * SR)
        y = np.zeros(total)
        for n, fl in zip(notes, flags):
            i0 = int(n.start * SR)
            if fl == "pluck":
                s = _ks(n.pitch, 0.6, muted=False)
            else:
                f0 = 440.0 * 2 ** ((n.pitch - 69) / 12)
                tt = np.arange(int(0.9 * SR)) / SR
                s = (0.4 * np.sin(2 * np.pi * f0 * tt)
                     * np.exp(-tt * 1.2)
                     * np.minimum(1.0, tt / 0.01))
            y[i0:i0 + len(s)] += s[:max(0, total - i0)]
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "g.wav"
            sf.write(str(wav), y, SR)
            _pm, harm = detect_techniques(notes, wav)
        got = [n.harmonic for n in notes]
        self.assertEqual(got[6:], [True, True], got)
        self.assertEqual(got[:6], [False] * 6, got)
        self.assertEqual(harm, 2)


@unittest.skipUnless(HAVE_GP, "PyGuitarPro is not installed")
class TestPalmMuteInGp5(unittest.TestCase):
    def test_harmonic_flag_written(self):
        import tempfile as tf

        from tabforge.core.fretboard import TabConfig, assign_tab
        from tabforge.export.writers import export_gp5
        notes = [NoteEvent(57, 0.0, 0.5, harmonic=True),
                 NoteEvent(57, 0.5, 0.5)]
        shapes = assign_tab(notes, TabConfig())
        with tf.TemporaryDirectory() as td:
            path = Path(td) / "x.gp5"
            export_gp5(shapes, path, TabConfig(), bpm=120.0)
            song = gp.parse(str(path))
        hs = [n.effect.harmonic is not None
              for m in song.tracks[0].measures
              for b in m.voices[0].beats
              if b.status == gp.BeatStatus.normal
              for n in b.notes]
        self.assertEqual(hs, [True, False])

@unittest.skipUnless(HAVE_GP, "PyGuitarPro is not installed")
class TestPalmMuteInGp5(unittest.TestCase):
    def test_pm_flag_written(self):
        import tempfile as tf

        from tabforge.core.fretboard import TabConfig, assign_tab
        from tabforge.export.writers import export_gp5
        notes = [NoteEvent(45, k * 0.5, 0.3,
                           palm_mute=(k % 2 == 0)) for k in range(4)]
        shapes = assign_tab(notes, TabConfig())
        with tf.TemporaryDirectory() as td:
            path = Path(td) / "x.gp5"
            export_gp5(shapes, path, TabConfig(), bpm=120.0)
            song = gp.parse(str(path))
        pm = [n.effect.palmMute
              for m in song.tracks[0].measures
              for b in m.voices[0].beats
              if b.status == gp.BeatStatus.normal
              for n in b.notes]
        self.assertEqual(pm, [True, False, True, False])


if __name__ == "__main__":
    unittest.main()
