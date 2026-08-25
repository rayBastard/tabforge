"""
What does this stem ACTUALLY sound like?

demucs names its outputs guitar/piano/other by role, not by listening:
an orchestral line routinely lands in the "guitar" stem and gets a fake
tablature. A small AudioSet tagger (PANNs CNN14) listens to the middle
of each stem and names the instruments it hears — the UI shows the
verdict and warns when it disagrees with the stem's name.

Everything here is OPTIONAL and graceful: if panns-inference is not
installed or the checkpoint cannot be fetched, tagging silently returns
nothing and the app behaves exactly as before.
"""

from __future__ import annotations

import os
import urllib.request
from pathlib import Path

PANNS_DIR = Path.home() / "panns_data"
_LABELS_URL = ("http://storage.googleapis.com/us_audioset/youtube_corpus/"
               "v1/csv/class_labels_indices.csv")
_CHECKPOINT_URL = ("https://zenodo.org/record/3987831/files/"
                   "Cnn14_mAP%3D0.431.pth")
_CHECKPOINT = PANNS_DIR / "Cnn14_mAP=0.431.pth"

# generic labels that say nothing about the instrument
_GENERIC = {
    "Music", "Musical instrument", "Song", "Melody", "Background music",
    "Silence", "Speech", "Inside, small room", "Inside, large room or hall",
    "Sad music", "Tender music", "Happy music", "Exciting music",
    "Scary music", "Angry music", "New-age music", "Christian music",
    "Wedding music", "Pop music", "Rock music", "Heavy metal",
    "Soundtrack music", "Video game music", "Theme music", "Jingle (music)",
    "Ambient music", "Electronic music", "Ringtone", "Sound effect",
}

_tagger = None      # loaded once per process


def _ensure_files() -> bool:
    """panns-inference downloads via wget, which macOS lacks — fetch the
    label table and the ~320 MB checkpoint ourselves, demucs-style."""
    try:
        PANNS_DIR.mkdir(parents=True, exist_ok=True)
        labels_csv = PANNS_DIR / "class_labels_indices.csv"
        if not labels_csv.exists():
            urllib.request.urlretrieve(_LABELS_URL, labels_csv)
        if not _CHECKPOINT.exists():
            urllib.request.urlretrieve(_CHECKPOINT_URL, _CHECKPOINT)
        return True
    except Exception:  # noqa: BLE001 — no network, no tagging
        return False


def tag_stem(wav: Path, clip_s: float = 20.0,
             min_prob: float = 0.08, top: int = 2) -> list[str]:
    """Top instrument labels heard in the middle of the stem, or []."""
    if os.environ.get("TABFORGE_NO_TAGGING"):
        return []
    global _tagger
    try:
        if _tagger is None:
            if not _ensure_files():
                return []
            from panns_inference import AudioTagging
            _tagger = AudioTagging(checkpoint_path=str(_CHECKPOINT),
                                   device="cpu")
        import librosa
        import numpy as np
        from panns_inference import labels

        y, sr = librosa.load(str(wav), sr=32000, mono=True)
        if not len(y):
            return []
        mid = len(y) // 2
        half = int(clip_s * sr / 2)
        clip = y[max(0, mid - half): mid + half]
        clipwise, _ = _tagger.inference(clip[None, :])
        ranked = np.argsort(clipwise[0])[::-1]
        out = []
        for i in ranked:
            label = labels[i]
            if label in _GENERIC:
                continue
            if float(clipwise[0][i]) < min_prob:
                break
            out.append(label)
            if len(out) >= top:
                break
        return out
    except Exception:  # noqa: BLE001 — tagging is best-effort decoration
        return []
