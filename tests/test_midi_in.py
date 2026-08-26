"""MIDI drop path (task: drop a .mid instead of audio)."""
import tempfile
import unittest
from pathlib import Path


def _make_midi(path):
    import pretty_midi
    pm = pretty_midi.PrettyMIDI(initial_tempo=100)
    guitar = pretty_midi.Instrument(program=27)
    for i in range(8):
        guitar.notes.append(pretty_midi.Note(
            velocity=90, pitch=52 + i, start=i * 0.6, end=i * 0.6 + 0.5))
    drums = pretty_midi.Instrument(program=0, is_drum=True)
    drums.notes.append(pretty_midi.Note(velocity=100, pitch=36,
                                        start=0.0, end=0.1))
    pm.instruments += [guitar, drums]
    pm.write(str(path))


class TestMidiDrop(unittest.TestCase):
    def test_classes_and_facts(self):
        from tabforge.audio.midi_in import (is_midi, load_midi_classes,
                                            midi_project_facts)
        with tempfile.TemporaryDirectory() as d:
            midi = Path(d) / "song.mid"
            _make_midi(midi)
            self.assertTrue(is_midi(midi))
            classes = load_midi_classes(midi)
            bpm, beats, dur = midi_project_facts(midi)
        self.assertEqual(sorted(classes), ["drums", "guitar"])
        self.assertEqual(len(classes["guitar"]), 8)
        self.assertAlmostEqual(bpm, 100.0, places=1)
        self.assertGreater(len(beats), 4)

    def test_analyze_midi_cards(self):
        from tabforge.pipeline import run_analyze_midi
        with tempfile.TemporaryDirectory() as d:
            midi = Path(d) / "song.mid"
            _make_midi(midi)
            res = run_analyze_midi(midi, Path(d) / "out")
        self.assertEqual(res.midi_source, midi)
        self.assertEqual(res.analysis["guitar"].status, "found")
        self.assertEqual(res.analysis["guitar"].note_count, 8)
        self.assertEqual(res.analysis["drums"].status, "found")
        self.assertEqual(res.analysis["bass"].status, "absent")
        self.assertAlmostEqual(res.bpm, 100.0, places=1)

    def test_transcribe_from_midi(self):
        from tabforge.pipeline import (PipelineOptions, run_analyze_midi,
                                       run_transcribe)
        with tempfile.TemporaryDirectory() as d:
            midi = Path(d) / "song.mid"
            _make_midi(midi)
            out = Path(d) / "out"
            res = run_analyze_midi(midi, out)
            results = run_transcribe(out, res, PipelineOptions(
                stems=("guitar", "drums"), subdivision=2,
                with_lyrics=False))
            names = {r.stem: r.note_count for r in results}
            self.assertEqual(names.get("guitar"), 8)
            self.assertIn("drums", names)
            self.assertTrue((out / "song" / "song.gp5").exists())


if __name__ == "__main__":
    unittest.main()
