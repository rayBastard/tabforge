"""Тесты ядра. Без ML-зависимостей — гоняются в CI за секунды."""
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
        # E4 (64): открытая 1-я, 5-й лад 2-й, 9-й лад 3-й, 14-й лад 4-й, 19-й лад 5-й
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

    def test_c_major_stays_in_first_position(self):
        shapes = assign_tab(seq([48, 50, 52, 53, 55, 57, 59, 60]))
        frets = [p.fret for s in shapes for p in s.placements]
        self.assertLessEqual(max(frets), 4, "гамма C-dur должна лечь в 1-ю позицию")

    def test_open_chords(self):
        # Em: должен найтись как 022000
        shapes = assign_tab(seq([[40, 47, 52, 55, 59, 64]], step=1.0))
        frets = sorted(p.fret for p in shapes[0].placements)
        self.assertEqual(frets, [0, 0, 0, 0, 2, 2])

    def test_solo_stays_in_box(self):
        shapes = assign_tab(seq([69, 72, 74, 76, 79, 76, 74, 72]))
        frets = [p.fret for s in shapes for p in s.placements]
        self.assertLessEqual(max(frets) - min(frets), 5,
                             "фраза должна лежать в одной позиции")

    def test_bass_tuning(self):
        cfg = TabConfig(tuning=TUNINGS["bass_4"], max_fret=20)
        shapes = assign_tab(seq([28, 31, 33, 35]), cfg)
        self.assertTrue(all(0 <= p.fret <= 20
                            for s in shapes for p in s.placements))

    def test_empty(self):
        self.assertEqual(assign_tab([]), [])


class TestAscii(unittest.TestCase):
    def test_render_has_six_lines(self):
        shapes = assign_tab(seq([48, 50]))
        text = render_ascii(shapes)
        self.assertEqual(len(text.splitlines()), 6)
        self.assertTrue(text.splitlines()[0].startswith("e|"))


if __name__ == "__main__":
    unittest.main()
