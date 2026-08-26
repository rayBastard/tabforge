"""Lyrics module (task 60): pure parts."""
import unittest

from tabforge.audio.lyrics import to_lrc


class TestLrc(unittest.TestCase):
    def test_lrc_format_and_hidden(self):
        lyrics = {"segments": [
            {"start": 2.9, "hidden": False, "junk": False,
             "words": [{"word": "Samus"}, {"word": "means"}]},
            {"start": 65.25, "hidden": True, "junk": True,
             "words": [{"word": "garbage"}]},
            {"start": 71.3, "hidden": False, "junk": False,
             "words": [{"word": "the"}, {"word": "end"}]},
        ]}
        lrc = to_lrc(lyrics)
        self.assertEqual(lrc.splitlines(),
                         ["[00:02.90]Samus means", "[01:11.30]the end"])

    def test_empty(self):
        self.assertEqual(to_lrc({"segments": []}), "")


if __name__ == "__main__":
    unittest.main()
