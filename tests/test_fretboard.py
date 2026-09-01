"""Core tests. No ML dependencies — they run in CI in seconds."""
import unittest

from tabforge.core.fretboard import (
    NoteEvent, TabConfig, TUNINGS, assign_tab, candidates_for_pitch,
    group_into_events, render_ascii, shapes_for_event,
)


def seq(pitches, step=0.25):
    out, t = [], 0.0
    for p in pitches:
        group = p if isinstance(p, (list, tuple)) else [p]
        out.extend(NoteEvent(x, t, step) for x in group)
        t += step
    return out


class TestCandidates(unittest.TestCase):
    def test_e4_has_three_places(self):
        # E4 (64): open 1st, 5th fret on 2nd, 9th on 3rd, 14th on 4th, 19th on 5th
        cands = candidates_for_pitch(64, TabConfig())
        self.assertIn((5, 0), cands)
        self.assertIn((4, 5), cands)
        self.assertIn((3, 9), cands)

    def test_out_of_range(self):
        self.assertEqual(candidates_for_pitch(20, TabConfig()), [])
        self.assertEqual(candidates_for_pitch(110, TabConfig()), [])


class TestGrouping(unittest.TestCase):
    def test_chord_grouped(self):
        notes = [NoteEvent(60, 0.0, 0.5), NoteEvent(64, 0.01, 0.5),
                 NoteEvent(67, 0.3, 0.5)]
        events = group_into_events(notes, tolerance=0.045)
        self.assertEqual([len(e) for e in events], [2, 1])


class TestShapes(unittest.TestCase):
    def test_no_two_notes_on_one_string(self):
        event = [NoteEvent(64, 0, 1), NoteEvent(65, 0, 1)]
        for shape in shapes_for_event(event, TabConfig()):
            strings = [p.string for p in shape.placements]
            self.assertEqual(len(strings), len(set(strings)))

    def test_stretch_limit(self):
        cfg = TabConfig(max_stretch=5)
        event = [NoteEvent(64, 0, 1), NoteEvent(65, 0, 1)]
        for shape in shapes_for_event(event, cfg):
            self.assertLessEqual(shape.span, cfg.max_stretch)


class TestAssign(unittest.TestCase):
    def test_pitches_preserved(self):
        notes = seq([48, 50, 52, 53, 55])
        shapes = assign_tab(notes)
        got = [p.note.pitch for s in shapes for p in s.placements]
        self.assertEqual(sorted(got), [48, 50, 52, 53, 55])
        cfg = TabConfig()
        for s in shapes:
            for p in s.placements:
                self.assertEqual(cfg.tuning[p.string] + p.fret, p.note.pitch)

    def test_c_major_stays_in_one_position(self):
        # The textbook said "first position"; 360 GuitarSet players
        # said position playing wins (tasks 67+70) and the user sided
        # with the players. The invariant that SURVIVES both worlds:
        # the scale sits in one coherent box, the hand does not crawl.
        shapes = assign_tab(seq([48, 50, 52, 53, 55, 57, 59, 60]))
        frets = [p.fret for s in shapes for p in s.placements if p.fret > 0]
        self.assertLessEqual(max(frets) - min(frets), 5,
                             "the C major scale should sit in one box")

    def test_open_chords(self):
        # Em: should be found as 022000
        shapes = assign_tab(seq([[40, 47, 52, 55, 59, 64]], step=1.0))
        frets = sorted(p.fret for p in shapes[0].placements)
        self.assertEqual(frets, [0, 0, 0, 0, 2, 2])

    def test_solo_stays_in_box(self):
        shapes = assign_tab(seq([69, 72, 74, 76, 79, 76, 74, 72]))
        frets = [p.fret for s in shapes for p in s.placements]
        self.assertLessEqual(max(frets) - min(frets), 5,
                             "the phrase should stay in one position")

    def test_bass_tuning(self):
        cfg = TabConfig(tuning=TUNINGS["bass_4"], max_fret=20)
        shapes = assign_tab(seq([28, 31, 33, 35]), cfg)
        self.assertTrue(all(0 <= p.fret <= 20
                            for s in shapes for p in s.placements))

    def test_empty(self):
        self.assertEqual(assign_tab([]), [])

    def test_unplayable_first_event_is_skipped(self):
        # A note below the instrument's range must not wipe out the tab.
        notes = [NoteEvent(10, 0.0, 0.25)] + seq([48, 50, 52], step=0.25)
        for n in notes[1:]:
            n.start += 0.25
        shapes = assign_tab(notes)
        got = [p.note.pitch for s in shapes for p in s.placements]
        self.assertEqual(sorted(got), [48, 50, 52])

    def test_unvoiceable_first_chord_is_written_not_eaten(self):
        # DOCTRINE CHANGE (calibration session 2, "ноты пропадают"):
        # an in-range cluster no hand can voice is WRITTEN with the
        # stretch relaxed rather than silently dropped — the score
        # must show every note; only out-of-range pitches still go.
        cfg = TabConfig(tuning=TUNINGS["bass_4"], max_fret=20)
        cluster = [NoteEvent(p, 0.0, 0.2) for p in (34, 57, 41, 37)]
        line = [NoteEvent(p, 1.0 + i * 0.5, 0.4)
                for i, p in enumerate((28, 31, 33, 35))]
        shapes = assign_tab(cluster + line, cfg)
        got = sorted(p.note.pitch for s in shapes for p in s.placements)
        self.assertEqual(got, [28, 31, 33, 34, 35, 37, 41, 57])
        self.assertEqual(len(shapes), 5)      # the cluster is ONE shape


class TestPins(unittest.TestCase):
    def test_pin_moves_the_note_and_its_neighbors(self):
        # E4-F4-G4... lays out around the open top strings by default;
        # pinning the first E4 onto the G string (fret 9) must pull the
        # NEIGHBORS up into the fret 9-12 box too — the surroundings
        # re-arrange around the pin instead of jumping back down.
        # this test exercises PIN MECHANICS, not layout preference —
        # the position prior (task 70) is off so the baseline lands on
        # the open strings and the pin's pull is unambiguous
        from dataclasses import replace
        cfg = replace(TabConfig(), pos_prior_weight=0.0)
        notes = seq([64, 65, 67, 65, 64], step=0.3)
        plain = assign_tab(notes, cfg)
        self.assertEqual(plain[0].placements[0].fret, 0,
                         "baseline must start on the open E for the "
                         "test to be meaningful")

        pinned = assign_tab(notes, cfg, pins={0: 3})
        first = pinned[0].placements[0]
        self.assertEqual(first.string, 3, "the pin is a hard constraint")
        self.assertEqual(first.fret, 9)
        # pitches survive untouched
        got = sorted(p.note.pitch for s in pinned for p in s.placements)
        self.assertEqual(got, sorted([64, 65, 67, 65, 64]))
        # immediate neighbors follow into the pin's box instead of the
        # open strings (the far-away last note may legitimately return)
        for s in pinned[1:4]:
            self.assertGreaterEqual(
                s.placements[0].fret, 5,
                "neighbors should re-arrange around the pin")

    def test_impossible_pin_drops_only_that_event(self):
        notes = seq([55, 59], step=0.3)
        # pitch 55 cannot live on the top E string (would need fret -9)
        shapes = assign_tab(notes, pins={0: 5})
        got = sorted(p.note.pitch for s in shapes for p in s.placements)
        self.assertEqual(got, [59], "unplayable pin drops that note only")


class TestAscii(unittest.TestCase):
    def test_render_has_six_lines(self):
        shapes = assign_tab(seq([48, 50]))
        text = render_ascii(shapes)
        self.assertEqual(len(text.splitlines()), 6)
        self.assertTrue(text.splitlines()[0].startswith("e|"))


if __name__ == "__main__":
    unittest.main()


class TestTuningCatalog(unittest.TestCase):
    def test_all_tunings_ascend_low_to_high(self):
        for name, tuning in TUNINGS.items():
            if name in ("ukulele", "notation_wide", "percussion"):
                continue
            self.assertEqual(list(tuning), sorted(tuning),
                             f"{name} must go low string -> high string")

    def test_downtuned_suggestions(self):
        from tabforge.pipeline import suggest_tuning
        self.assertEqual(suggest_tuning("guitar", 40), "standard")
        self.assertEqual(suggest_tuning("guitar", 38), "drop_d")
        self.assertEqual(suggest_tuning("guitar", 36), "drop_c")
        # drop A territory is 7-string territory, as players expect;
        # anything lower clamps there — gp5 cannot write an 8th string
        self.assertEqual(suggest_tuning("guitar", 33), "seven_drop_a")
        self.assertEqual(suggest_tuning("guitar", 31), "seven_drop_a")
        self.assertEqual(suggest_tuning("guitar", 25), "seven_drop_a")
        self.assertEqual(suggest_tuning("bass", 27), "bass_5")
