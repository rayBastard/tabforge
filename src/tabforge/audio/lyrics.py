"""
Synced lyrics (task 60).

faster-whisper (CTranslate2, MIT — a clean pip extra, unlike the
gated NC models) transcribes the SEPARATED VOCAL STEM with word-level
timestamps. Suno's generative singing includes pseudo-words, so the
honest contract is usefulness, not accuracy: every segment carries a
junk score (no-speech probability + decoder confidence), the UI dims
suspicious segments and lets the human hide them in one click — the
words we keep are aligned, the words we doubt are marked, nothing is
silently invented.

Words attach to the beat grid loosely: a word without a note keeps its
own time (per the plan — no aggressive repair).
"""

from __future__ import annotations

import os
from pathlib import Path

MODEL = os.environ.get("TABFORGE_WHISPER_MODEL", "small")


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def transcribe_lyrics(vocals: Path, language: str | None = None,
                      progress=lambda *_: None) -> dict | None:
    """{'language': ..., 'segments': [{start, end, junk, words:
    [{word, start, end, prob}]}]} or None without the extra."""
    if not available():
        return None
    from faster_whisper import WhisperModel

    progress("transcribe",
             f"lyrics: transcribing the vocals (whisper-{MODEL})")
    model = WhisperModel(MODEL, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        str(vocals), language=language, word_timestamps=True,
        vad_filter=True)
    out = {"language": info.language, "segments": []}
    for seg in segments:
        words = [{"word": w.word.strip(), "start": float(w.start),
                  "end": float(w.end), "prob": round(float(w.probability), 3)}
                 for w in (seg.words or []) if w.word.strip()]
        if not words:
            continue
        junk = (float(seg.no_speech_prob) > 0.5
                or float(seg.avg_logprob) < -1.0)
        out["segments"].append({
            "start": float(seg.start), "end": float(seg.end),
            "junk": junk, "hidden": False, "words": words,
        })
    return out


def to_lrc(lyrics: dict) -> str:
    """The .lrc standard: [mm:ss.xx] line per visible segment."""
    lines = []
    for seg in lyrics.get("segments", []):
        if seg.get("hidden"):
            continue
        m, s = divmod(seg["start"], 60)
        text = " ".join(w["word"] for w in seg["words"])
        lines.append(f"[{int(m):02d}:{s:05.2f}]{text}")
    return "\n".join(lines) + "\n" if lines else ""
