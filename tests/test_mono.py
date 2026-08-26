"""Monophonic transcription path (task 53): synthetic-signal checks."""
import unittest

import numpy as np

from tabforge.audio.mono import HOP, SR, transcribe_mono
from tabforge.core.fretboard import NoteEvent


def _tone(midi: float, dur_s: float, amp: float = 0.4) -> np.ndarray:
    f = 440.0 * 2 ** ((midi - 69) / 12)
    t = np.arange(int(SR * dur_s)) / SR
    # a little second harmonic so the onset detector sees an attack
    y = amp * (np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t))
    edge = int(0.01 * SR)
    env = np.ones_like(y)
    env[:edge] = np.linspace(0, 1, edge)
    env[-edge:] = np.linspace(1, 0, edge)
    return y * env


def _write(tmp, y):
    import soundfile as sf
    sf.write(str(tmp), y.astype(np.float32), SR)


class TestMonoPath(unittest.TestCase):
    def _transcribe(self, y, **kw):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "stem.wav"
            _write(wav, y)
            return transcribe_mono(wav, **kw)

    def test_melody_pitches(self):
        gap = np.zeros(int(0.06 * SR))
        y = np.concatenate([_tone(45, 0.4), gap, _tone(48, 0.4),
                            gap, _tone(43, 0.4)])
        notes = self._transcribe(y, fmin=40.0, fmax=500.0)
        pitched = [n.pitch for n in notes if not n.dead]
        self.assertEqual(pitched, [45, 48, 43])
        self.assertTrue(all(not n.dead for n in notes))

    def test_recitative_marks_unstable_as_dead(self):
        # speech-like babble: f0 random-walks (jitter, nothing held
        # within a semitone) and 30 ms consonant stops cut it into
        # ~100 ms syllables -> recitative mode makes it dead notes
        rng = np.random.default_rng(7)
        n = int(SR * 0.8)
        walk = np.empty(n // 128 + 1)
        v = 0.0
        for i, e in enumerate(rng.normal(0, 0.9, len(walk))):
            v = 0.9 * v + e            # mean-reverting: wanders ±2 st
            walk[i] = v                # without saturating anywhere
        semis = np.repeat(walk, 128)[:n]
        f = 220.0 * 2 ** (semis / 12)
        babble = 0.4 * np.sin(2 * np.pi * np.cumsum(f) / SR)
        syl = int(0.13 * SR)
        stop = int(0.03 * SR)
        for i in range(0, n - stop, syl):
            babble[i:i + stop] = 0.0
        gap = np.zeros(int(0.08 * SR))
        y = np.concatenate([_tone(57, 0.4), gap, babble])
        notes = self._transcribe(y, fmin=70.0, fmax=1100.0,
                                 recitative=True, stable_ms=100.0)
        self.assertTrue(any(not n.dead for n in notes))
        self.assertTrue(any(n.dead for n in notes),
                        f"no dead notes in {notes}")
        # the same babble WITHOUT recitative mode yields no dead notes
        notes2 = self._transcribe(y, fmin=70.0, fmax=1100.0)
        self.assertTrue(all(not n.dead for n in notes2))

    def test_legato_split_without_onset(self):
        # two pitches joined seamlessly (no attack): the pitch-jump rule
        # must still split them into two notes
        y = np.concatenate([_tone(45, 0.5), _tone(50, 0.5)])
        notes = self._transcribe(y, fmin=40.0, fmax=500.0)
        pitched = [n.pitch for n in notes if not n.dead]
        self.assertIn(45, pitched)
        self.assertIn(50, pitched)


class TestDeadNoteExport(unittest.TestCase):
    def test_dead_note_written_as_dead_type(self):
        import tempfile
        from pathlib import Path

        import guitarpro as gp

        from tabforge.core import TabConfig, TUNINGS
        from tabforge.core.fretboard import assign_tab
        from tabforge.core.instruments import profile_for
        from tabforge.export.writers import export_song_gp5, SongPart

        notes = [NoteEvent(45, 0.0, 0.4),
                 NoteEvent(45, 0.5, 0.4, dead=True)]
        cfg = TabConfig(tuning=TUNINGS["notation_wide"], max_fret=24)
        part = SongPart("vocals", assign_tab(notes, cfg), cfg,
                        profile_for("vocals"))
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "song.gp5"
            export_song_gp5([part], path, bpm=120.0)
            song = gp.parse(str(path))
        types = [note.type
                 for m in song.tracks[0].measures
                 for v in m.voices for b in v.beats for note in b.notes]
        self.assertIn(gp.NoteType.dead, types)
        self.assertIn(gp.NoteType.normal, types)


if __name__ == "__main__":
    unittest.main()
