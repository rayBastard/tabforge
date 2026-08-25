"""
Аудио -> ноты. Здесь мы НИЧЕГО не изобретаем: берём state-of-the-art модели.

Цепочка:
  1. Demucs  — разделить микс на партии (гитара, бас, вокал, барабаны)
  2. Basic Pitch (Spotify) — полифоническая транскрипция каждой партии в ноты
  3. librosa — темп и сетка долей
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ..core.fretboard import NoteEvent

# Инструменты, которые умеет выделять htdemucs_6s
SIX_STEMS = ("drums", "bass", "other", "vocals", "guitar", "piano")


def separate_stems(audio: Path, out_dir: Path, model: str = "htdemucs_6s") -> dict[str, Path]:
    """Разделяет микс на партии. Возвращает {имя_партии: путь_к_wav}."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "demucs", "-n", model, "-o", str(out_dir), str(audio)]
    subprocess.run(cmd, check=True)

    stem_dir = out_dir / model / audio.stem
    return {p.stem: p for p in stem_dir.glob("*.wav")}


def transcribe_stem(
    audio: Path,
    *,
    onset_threshold: float = 0.5,
    frame_threshold: float = 0.3,
    min_note_length_ms: float = 68.0,
    min_freq: float | None = None,
    max_freq: float | None = None,
) -> list[NoteEvent]:
    """Одна партия -> список нот. Пороги подбираются под инструмент."""
    from basic_pitch.inference import predict

    _model_out, _midi, note_events = predict(
        str(audio),
        onset_threshold=onset_threshold,
        frame_threshold=frame_threshold,
        minimum_note_length=min_note_length_ms,
        minimum_frequency=min_freq,
        maximum_frequency=max_freq,
        melodia_trick=True,
    )

    notes = [
        NoteEvent(
            pitch=int(pitch),
            start=float(start),
            duration=max(float(end) - float(start), 0.02),
            velocity=max(1, min(127, int(amplitude * 127))),
        )
        for start, end, pitch, amplitude, *_ in note_events
    ]
    return sorted(notes, key=lambda n: (n.start, n.pitch))


# Пресеты порогов: у баса ноты длинные и низкие, у соло-гитары — короткие
PRESETS: dict[str, dict] = {
    "bass":   dict(onset_threshold=0.45, frame_threshold=0.25,
                   min_note_length_ms=90, min_freq=30, max_freq=400),
    "guitar": dict(onset_threshold=0.55, frame_threshold=0.32,
                   min_note_length_ms=60, min_freq=70, max_freq=1400),
    "vocals": dict(onset_threshold=0.5, frame_threshold=0.3,
                   min_note_length_ms=90, min_freq=80, max_freq=1200),
    "piano":  dict(onset_threshold=0.5, frame_threshold=0.3,
                   min_note_length_ms=60),
    "other":  dict(),
}


def detect_tempo(audio: Path) -> tuple[float, list[float]]:
    """Возвращает (BPM, времена долей в секундах)."""
    import librosa

    y, sr = librosa.load(str(audio), mono=True)
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time", trim=False)
    return float(tempo), [float(b) for b in beats]


def cleanup(notes: list[NoteEvent], *, min_duration: float = 0.05,
            max_polyphony: int = 6) -> list[NoteEvent]:
    """
    Basic Pitch любит выдавать призрачные обертоны. Убираем:
      - слишком короткие огрызки,
      - лишние ноты в слишком густых созвучиях (оставляем самые громкие).
    """
    notes = [n for n in notes if n.duration >= min_duration]
    notes.sort(key=lambda n: (n.start, -n.velocity))

    out: list[NoteEvent] = []
    i = 0
    while i < len(notes):
        j = i
        while j < len(notes) and notes[j].start - notes[i].start < 0.045:
            j += 1
        chunk = notes[i:j]
        seen: set[int] = set()
        kept = []
        for n in chunk:
            if n.pitch in seen:
                continue
            seen.add(n.pitch)
            kept.append(n)
        out.extend(kept[:max_polyphony])
        i = j
    return out
