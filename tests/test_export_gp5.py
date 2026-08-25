"""Round-trip tests for the .gp5 export. Skipped when PyGuitarPro
is not installed (CI runs the core without the export extra)."""
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from tabforge.core.fretboard import NoteEvent, TabConfig, TUNINGS, assign_tab

try:
    import guitarpro as gp
    HAVE_GP = True
except ImportError:
    HAVE_GP = False


def _roundtrip(shapes, cfg, **kwargs):
    from tabforge.export.writers import export_gp5

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test.gp5"
        export_gp5(shapes, path, cfg, **kwargs)
        song = gp.parse(str(path))
    track = song.tracks[0]
    string_value = {s.number: s.value for s in track.strings}
    beats = [b for m in track.measures for v in m.voices for b in v.beats]
    pitches = [string_value[n.string] + n.value for b in beats for n in b.notes]
    return track, beats, pitches


@unittest.skipUnless(HAVE_GP, "PyGuitarPro is not installed")
class TestGp5Roundtrip(unittest.TestCase):
    def setUp(self):
        self.cfg = TabConfig()

    def test_pitches_survive_roundtrip(self):
        source = [60, 62, 64, 65, 67, 69, 71, 72]
        notes = [NoteEvent(p, i * 0.5, 0.4) for i, p in enumerate(source)]
        shapes = assign_tab(notes, self.cfg)
        _, beats, pitches = _roundtrip(shapes, self.cfg, bpm=120.0)
        self.assertEqual(Counter(pitches), Counter(source))

    def test_beats_stay_separate(self):
        # Eight notes inside one measure must come back as eight beats,
        # not collapse into one (the BeatStatus.empty regression).
        notes = [NoteEvent(60, i * 0.2, 0.15) for i in range(8)]
        shapes = assign_tab(notes, self.cfg)
        _, beats, _ = _roundtrip(shapes, self.cfg, bpm=120.0)
        self.assertEqual(len(beats), len(shapes))
        for beat in beats:
            strings = [n.string for n in beat.notes]
            self.assertEqual(len(strings), len(set(strings)),
                             "two notes on one string within a beat")

    def test_chord_kept_in_one_beat(self):
        chord = [40, 47, 52, 55, 59, 64]  # open Em
        notes = [NoteEvent(p, 0.0, 1.0) for p in chord]
        shapes = assign_tab(notes, self.cfg)
        _, beats, pitches = _roundtrip(shapes, self.cfg, bpm=120.0)
        self.assertEqual(len(beats), 1)
        self.assertEqual(Counter(pitches), Counter(chord))

    def test_bass_tuning(self):
        cfg = TabConfig(tuning=TUNINGS["bass_4"], max_fret=20)
        source = [28, 31, 33, 35]
        notes = [NoteEvent(p, i * 0.5, 0.4) for i, p in enumerate(source)]
        shapes = assign_tab(notes, cfg)
        track, _, pitches = _roundtrip(shapes, cfg, bpm=100.0)
        self.assertEqual(len(track.strings), 4)
        self.assertEqual(Counter(pitches), Counter(source))


if __name__ == "__main__":
    unittest.main()
