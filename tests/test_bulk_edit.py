"""Mass editor ops (task 55): pins/legato survive index remapping."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tabforge import pipeline


def _state():
    def note(pitch, start):
        return {"pitch": pitch, "start": start, "duration": 0.4,
                "velocity": 90, "bends": [], "dead": False, "conf": 0.9}
    return {
        "guitar": {
            # 0:E3@0  1:E4@0 (octave double)  2:G3@1  3:A3@2  4:B3@3
            "notes": [note(52, 0.0), note(64, 0.0), note(55, 1.0),
                      note(57, 2.0), note(59, 3.0)],
            "legato": [[2, 3, "hammer"], [3, 4, "hammer"]],
            "pins": {"2": 1, "4": 2},
            "tuning": "standard", "profile": "guitar",
        },
        "bass": {
            "notes": [note(28, 0.5), note(31, 2.5)],
            "legato": [[0, 1, "hammer"]],
            "pins": {"1": 0},
            "tuning": "bass_4", "profile": "bass",
        },
    }


class _Shared:
    bpm = 120.0
    beats = []          # no grid: tick = start / (60/bpm/subdivision)
    key = None


class TestBulkEdit(unittest.TestCase):
    def _run(self, state, part, t0, t1, op, target=None):
        opts = pipeline.PipelineOptions(subdivision=2)
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            pipeline._parts_file(out).write_text(json.dumps(state))
            with mock.patch.object(pipeline, "_rebuild_outputs",
                                   return_value={}):
                result = pipeline.apply_bulk_edit(
                    out, part, t0, t1, op, _Shared(), opts,
                    target_part=target)
            return result, json.loads(pipeline._parts_file(out).read_text())

    # ticks at 120 BPM subdivision 2: tick = start * 4
    def test_octave_shift(self):
        result, state = self._run(_state(), "guitar", 4, 8, "octave_down")
        pitches = [n["pitch"] for n in state["guitar"]["notes"]]
        self.assertEqual(pitches, [52, 64, 43, 45, 59])  # notes @1s,2s
        self.assertEqual(result["count"], 2)

    def test_delete_remaps_pins_and_legato(self):
        _, state = self._run(_state(), "guitar", 4, 4, "delete")  # G3@1
        g = state["guitar"]
        self.assertEqual([n["pitch"] for n in g["notes"]],
                         [52, 64, 57, 59])
        # pin that lived on index 2 (deleted) is gone; pin on 4 -> 3
        self.assertEqual(g["pins"], {"3": 2})
        # legato pair (2,3) died with its note; (3,4) -> (2,3)
        self.assertEqual(g["legato"], [[2, 3, "hammer"]])

    def test_dedup_octaves_upper_wins(self):
        _, state = self._run(_state(), "guitar", 0, 20, "dedup_octaves")
        pitches = [n["pitch"] for n in state["guitar"]["notes"]]
        self.assertNotIn(52, pitches)   # the lower twin died
        self.assertIn(64, pitches)

    def test_dedup_without_doubles_is_an_error(self):
        with self.assertRaises(ValueError):
            self._run(_state(), "guitar", 4, 20, "dedup_octaves")

    def test_reassign_moves_and_reorders(self):
        _, state = self._run(_state(), "guitar", 4, 8, "reassign",
                             target="bass")
        self.assertEqual([n["pitch"] for n in state["guitar"]["notes"]],
                         [52, 64, 59])
        # bass gets G3@1 and A3@2 merged IN TIME ORDER
        self.assertEqual([n["pitch"] for n in state["bass"]["notes"]],
                         [28, 55, 57, 31])
        # bass's old pin on index 1 (note @2.5s) follows it to index 3
        self.assertEqual(state["bass"]["pins"], {"3": 0})
        self.assertEqual(state["bass"]["legato"], [[0, 3, "hammer"]])

    def test_empty_selection_is_an_error(self):
        with self.assertRaises(ValueError):
            self._run(_state(), "guitar", 100, 120, "delete")

    def test_unknown_op_is_an_error(self):
        with self.assertRaises(ValueError):
            self._run(_state(), "guitar", 0, 4, "transmogrify")


class TestReferenceExport(unittest.TestCase):
    def test_golden_corpus_naming(self):
        import zipfile
        state = _state()
        state["guitar_lead"] = dict(state["guitar"])
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            pipeline._parts_file(out).write_text(json.dumps(state))
            zip_path = pipeline.export_reference(out, "MyTrack")
            names = sorted(zipfile.ZipFile(zip_path).namelist())
        self.assertEqual(names, ["MyTrack (Bass).mid",
                                 "MyTrack (Guitar) (2).mid",
                                 "MyTrack (Guitar).mid"])

    def test_reference_loads_as_golden_truth(self):
        """The exported files must round-trip through the golden
        loader's parsing convention."""
        import re
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            pipeline._parts_file(out).write_text(json.dumps(_state()))
            pipeline.export_reference(out, "MyTrack")
            for mid in (out / "reference").glob("*.mid"):
                groups = re.findall(r"\(([^)]+)\)", mid.stem)
                inst = next((g.strip().lower() for g in groups
                             if g.strip().isalpha()), None)
                self.assertIn(inst, ("guitar", "bass"))


if __name__ == "__main__":
    unittest.main()
