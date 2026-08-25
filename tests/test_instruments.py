"""Instrument profile tests: pianos must not grow bends."""
import math
import tempfile
import unittest
from pathlib import Path

from tabforge.core.fretboard import NoteEvent, TUNINGS, TabConfig, assign_tab
from tabforge.core.instruments import profile_for

try:
    import guitarpro as gp
    HAVE_GP = True
except ImportError:
    HAVE_GP = False


class TestProfiles(unittest.TestCase):
    def test_families(self):
        self.assertTrue(profile_for("guitar").allow_bends)
        self.assertTrue(profile_for("guitar_lead").allow_bends)
        self.assertTrue(profile_for("guitar_rhythm").tablature)
        self.assertEqual(profile_for("bass").tuning, "bass_4")

    def test_midi_programs(self):
        self.assertEqual(profile_for("guitar").midi_program, 25)
        self.assertEqual(profile_for("guitar_lead").midi_program, 27)
        self.assertEqual(profile_for("guitar_rhythm").midi_program, 25)
        self.assertEqual(profile_for("bass").midi_program, 33)
        self.assertEqual(profile_for("piano").midi_program, 0)
        self.assertEqual(profile_for("vocals").midi_program, 52)
        self.assertFalse(profile_for("piano").tablature)
        self.assertTrue(profile_for("piano").legato_as_slur)
        vocals = profile_for("vocals")
        self.assertTrue(vocals.allow_slides)
        self.assertFalse(vocals.allow_bends)
        # unknown stems keep the historical guitar treatment
        self.assertTrue(profile_for("other").tablature)

    def test_drums_are_percussion(self):
        drums = profile_for("drums")
        self.assertTrue(drums.percussion)
        self.assertFalse(drums.tablature)
        self.assertEqual(drums.tuning, "percussion")
        self.assertFalse(drums.allow_bends)
        # nothing else is percussion
        self.assertFalse(profile_for("guitar").percussion)
        self.assertFalse(profile_for("piano").percussion)

    def test_notation_wide_covers_the_piano_range(self):
        cfg = TabConfig(tuning=TUNINGS["notation_wide"], max_fret=24)
        for pitch in (26, 36, 60, 84):     # D1 .. C6
            shapes = assign_tab([NoteEvent(pitch, 0.0, 0.5)], cfg)
            self.assertEqual(len(shapes), 1, f"pitch {pitch} must encode")


def _busy_notes():
    """Notes carrying every articulation trigger at once."""
    rise_fall = [i * 0.1 for i in range(10)] + [1.0 - i * 0.1 for i in range(10)]
    vib = [0.3 * math.sin(2 * math.pi * i / 8) for i in range(32)]
    return [
        NoteEvent(55, 0.0, 0.28, 100), NoteEvent(59, 0.3, 0.5, 70),   # legato
        NoteEvent(64, 1.0, 0.9, 90, bends=vib),                        # vibrato
        NoteEvent(67, 2.0, 0.9, 90, bends=rise_fall),                  # bend
        NoteEvent(62, 3.0, 0.9, 90,
                  bends=[i * 0.05 for i in range(20)]),                # slide
    ]


@unittest.skipUnless(HAVE_GP, "PyGuitarPro is not installed")
class TestProfileGatingInGp5(unittest.TestCase):
    def _export(self, stem):
        from tabforge.core.articulation import detect_legato_pairs
        from tabforge.export.gp5_read import read_gp5
        from tabforge.export.writers import export_gp5

        profile = profile_for(stem)
        notes = _busy_notes()
        cfg = TabConfig(tuning=TUNINGS[profile.tuning or "standard"],
                        max_fret=profile.max_fret)
        legato = (detect_legato_pairs(notes)
                  if profile.wants_legato_pairs else [])
        shapes = assign_tab(notes, cfg,
                            legato=legato if profile.allow_hammer else None)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.gp5"
            export_gp5(shapes, path, cfg, bpm=120.0, legato=legato,
                       profile=profile)
            return read_gp5(str(path))

    def test_track_carries_the_profile_program(self):
        for stem, program in (("piano", 0), ("bass", 33), ("guitar", 25),
                              ("vocals", 52)):
            contents = self._export(stem)
            self.assertEqual(contents.track.channel.instrument, program,
                             f"{stem} must play as program {program}")

    def test_piano_gets_slurs_but_no_string_techniques(self):
        fx = self._export("piano").effects
        self.assertEqual(fx["bend"], 0, "a piano cannot bend")
        self.assertEqual(fx["slide"], 0, "a piano cannot slide")
        self.assertEqual(fx["vibrato"], 0)
        self.assertEqual(fx["hammer"], 1, "legato must become a slur")

    def test_vocals_keep_slides_only(self):
        fx = self._export("vocals").effects
        self.assertEqual(fx["bend"], 0)
        self.assertEqual(fx["vibrato"], 0)
        self.assertGreaterEqual(fx["slide"], 1, "portamento is real")
        self.assertEqual(fx["hammer"], 1, "legato slur")

    def test_guitar_keeps_everything(self):
        fx = self._export("guitar").effects
        self.assertGreaterEqual(fx["bend"], 1)
        self.assertGreaterEqual(fx["vibrato"], 1)
        self.assertGreaterEqual(fx["slide"], 1)


if __name__ == "__main__":
    unittest.main()
