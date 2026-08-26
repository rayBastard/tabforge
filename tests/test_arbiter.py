"""MT3 arbiter verdicts (task 54): the golden-corpus acceptance matrix.

Densities, self-tag probabilities and leak shares below are the REAL
measured values from the three golden tracks (docs/eval.md) — these
tests pin the tuned thresholds to the acceptance criteria:
Fulgrim = piano+strings, NO guitar, no drums; Loken = metal keeps its
guitar even though MT3 is blind to it (12 notes of 6890).
"""
import unittest
from unittest import mock

from tabforge.audio import arbiter


def _ev(value: bool):
    return lambda: value


class TestJudge(unittest.TestCase):
    # --- Fulgrim (piano piece, 1.1 min) ---
    def test_fulgrim_piano_found(self):
        self.assertEqual(
            arbiter.judge("piano", 244 / 1.1, "found", _ev(False)),
            "found")

    def test_fulgrim_guitar_phantom_absent(self):
        # MT3 16 notes, stem energetic, but Guitar prob 0.21 < 0.4
        self.assertEqual(
            arbiter.judge("guitar", 16 / 1.1, "found", _ev(False)),
            "absent")

    def test_fulgrim_bass_phantom_absent(self):
        # sample leak share 0.135 > 0.10: the "bass" is the piano's
        # left hand bleeding through demucs
        self.assertEqual(
            arbiter.judge("bass", 4 / 1.1, "found", _ev(False)),
            "absent")

    def test_fulgrim_drums_absent(self):
        # 49 MT3 events < 60/min for drums; kit probs 0.03
        self.assertEqual(
            arbiter.judge("drums", 49 / 1.1, "found", _ev(False)),
            "absent")

    def test_fulgrim_strings_found_as_other(self):
        self.assertEqual(
            arbiter.judge("other", 246 / 1.1, "found", _ev(False)),
            "found")

    def test_fulgrim_vocals_silent_stem(self):
        self.assertEqual(
            arbiter.judge("vocals", 0.0, "absent", _ev(True)),
            "absent")

    # --- Loken (metal, 4.9 min): the blindness guard ---
    def test_loken_guitar_kept_despite_blind_mt3(self):
        # MT3 heard 12 of 6890 guitar notes; Guitar prob 0.80 -> keep
        self.assertEqual(
            arbiter.judge("guitar", 12 / 4.9, "found", _ev(True)),
            "uncertain")

    def test_loken_bass_kept(self):
        # MT3 14 notes; leak share 0.04 -> real bass
        self.assertEqual(
            arbiter.judge("bass", 14 / 4.9, "found", _ev(True)),
            "uncertain")

    def test_loken_drums_found(self):
        self.assertEqual(
            arbiter.judge("drums", 2020 / 4.9, "found", _ev(False)),
            "found")

    def test_loken_vocals_kept(self):
        # MT3 put the voice in "other"; Singing+Speech+Rap 0.53
        self.assertEqual(
            arbiter.judge("vocals", 0.0, "found", _ev(True)),
            "uncertain")

    # --- Hero ---
    def test_hero_guitar_found(self):
        self.assertEqual(
            arbiter.judge("guitar", 675 / 6.4, "found", _ev(False)),
            "found")

    def test_other_never_unchecked_when_energetic(self):
        # the catch-basin has no self-identity: never auto-uncheck it
        self.assertEqual(
            arbiter.judge("other", 0.0, "quiet", _ev(False)),
            "uncertain")


class TestGracefulDegradation(unittest.TestCase):
    def test_no_env_no_defaults_no_arbiter(self):
        with mock.patch.dict("os.environ", {}, clear=True), \
             mock.patch.object(arbiter, "_DEFAULT_MT3_DIRS", ()):
            self.assertIsNone(arbiter.find_mt3())

    def test_run_mt3_without_install(self):
        import tempfile
        from pathlib import Path
        with mock.patch.dict("os.environ", {}, clear=True), \
             mock.patch.object(arbiter, "_DEFAULT_MT3_DIRS", ()), \
             tempfile.TemporaryDirectory() as d:
            self.assertIsNone(
                arbiter.run_mt3(Path(d) / "mix.wav", Path(d)))

    def test_default_location_is_probed(self):
        """A Finder-launched app has no shell env — the standard
        install location must be found without TABFORGE_MT3_DIR."""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "mt3"
            (root / "ymt3space").mkdir(parents=True)
            py = root / "venv-mt3" / "bin" / "python"
            py.parent.mkdir(parents=True)
            py.touch()
            with mock.patch.dict("os.environ", {}, clear=True), \
                 mock.patch.object(arbiter, "_DEFAULT_MT3_DIRS",
                                   (str(root),)):
                found = arbiter.find_mt3()
        self.assertIsNotNone(found)
        self.assertEqual(found[0].name, "ymt3space")


class TestMt3Card(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(arbiter._mt3_card(0, True), "drums")
        self.assertEqual(arbiter._mt3_card(0, False), "piano")
        self.assertEqual(arbiter._mt3_card(25, False), "guitar")
        self.assertEqual(arbiter._mt3_card(33, False), "bass")
        self.assertEqual(arbiter._mt3_card(52, False), "vocals")
        self.assertEqual(arbiter._mt3_card(48, False), "other")  # strings
        self.assertEqual(arbiter._mt3_card(81, False), "other")  # synth


class TestVerdictsFlow(unittest.TestCase):
    def test_full_loken_matrix(self):
        """verdicts() end to end with mocked MT3/tagger/leak — the
        Loken acceptance: guitar survives, piano goes."""
        from pathlib import Path

        densities = {"drums": 2020 / 4.9, "other": 578 / 4.9,
                     "guitar": 12 / 4.9, "bass": 14 / 4.9}
        statuses = {"guitar": "found", "bass": "found", "piano": "absent",
                    "vocals": "found", "other": "quiet", "drums": "found"}
        probs = {"guitar": {"Guitar": 0.802},
                 "vocals": {"Singing": 0.135, "Speech": 0.268,
                            "Rapping": 0.127},
                 "drums": {"Drum kit": 0.349},
                 "piano": {"Piano": 0.001}}
        with mock.patch.object(arbiter, "run_mt3",
                               return_value=Path("x.mid")), \
             mock.patch.object(arbiter, "mt3_densities",
                               return_value=densities), \
             mock.patch.object(arbiter, "_bass_leak_share",
                               return_value=0.04), \
             mock.patch("tabforge.audio.tagging.tag_probs",
                        side_effect=lambda w, wanted: probs.get(
                            Path(w).stem, {})):
            stems = {s: Path(f"{s}.wav") for s in statuses}
            out = arbiter.verdicts(Path("mix.wav"), stems, statuses,
                                   4.9, Path("."))
        self.assertEqual(out["guitar"], "uncertain")   # metal survives
        self.assertEqual(out["bass"], "uncertain")
        self.assertEqual(out["drums"], "found")
        self.assertEqual(out["vocals"], "uncertain")
        self.assertEqual(out["piano"], "absent")
        self.assertEqual(out["other"], "found")


if __name__ == "__main__":
    unittest.main()
