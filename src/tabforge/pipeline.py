"""
The single pipeline: file -> stems -> notes -> fingering -> output files.
Called by the CLI, the server, and the desktop app — the logic lives in one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .core.fretboard import TUNINGS, TabConfig, assign_tab, render_ascii
from .core.quantize import Grid, quantize

ProgressFn = Callable[[str, str], None]  # (stage, message)

STEM_TUNING = {"bass": "bass_4"}
STEM_PROGRAM = {"bass": 33, "guitar": 25, "vocals": 25, "piano": 0, "other": 25}

STAGES = ("separate", "tempo", "transcribe", "fingering", "export")


@dataclass(slots=True)
class PipelineOptions:
    stems: tuple[str, ...] = ("guitar", "bass")
    tuning: str = "standard"
    subdivision: int = 4
    quantize_strength: float = 0.9
    separate: bool = True          # False = transcribe the whole mix


@dataclass(slots=True)
class StemResult:
    stem: str
    bpm: float
    key: str            # e.g. "F minor"
    note_count: int
    ascii_tab: str
    files: dict[str, Path] = field(default_factory=dict)  # ext -> path


def _noop(_stage: str, _msg: str) -> None:
    pass


def run_pipeline(audio: Path, out_dir: Path,
                 opts: PipelineOptions | None = None,
                 progress: ProgressFn = _noop) -> list[StemResult]:
    from .audio import keydetect, transcribe
    from .export import writers

    opts = opts or PipelineOptions()
    out_dir.mkdir(parents=True, exist_ok=True)

    if opts.separate:
        progress("separate", "Separating into stems (first run downloads the model)")
        all_stems = transcribe.separate_stems(audio, out_dir / "stems")
        stems = {k: v for k, v in all_stems.items() if k in opts.stems}
        # Drums carry the clearest attacks, so the shared grid comes from
        # them; the tempo must be computed once or stems drift apart.
        tempo_source = all_stems.get("drums", audio)
        source_name = "drums" if "drums" in all_stems else "mix"
    else:
        stems = {"mix": audio}
        tempo_source, source_name = audio, "mix"

    progress("tempo", f"tempo and beat grid ({source_name})")
    bpm, beats = transcribe.detect_tempo(tempo_source)
    grid = Grid(beats, subdivision=opts.subdivision) if len(beats) > 1 else None

    # Key is track-global, so it comes from the full mix, not a stem.
    progress("tempo", "detecting the key")
    key = keydetect.detect_key(audio)
    progress("tempo", f"key: {key.name}")

    results: list[StemResult] = []
    for name, wav in stems.items():
        progress("transcribe", f"{name}: detecting notes")
        notes = transcribe.transcribe_stem(wav, **transcribe.PRESETS.get(name, {}))
        notes = transcribe.cleanup(
            notes, max_polyphony=1 if name == "bass" else 6)
        if not notes:
            progress("transcribe", f"{name}: no notes found, skipped")
            continue

        if grid is not None:
            notes = quantize(notes, grid, strength=opts.quantize_strength)

        progress("fingering", f"{name}: choosing the fingering")
        cfg = TabConfig(tuning=TUNINGS[STEM_TUNING.get(name, opts.tuning)])
        shapes = assign_tab(notes, cfg)

        progress("export", f"{name}: writing files")
        stem_dir = out_dir / name
        stem_dir.mkdir(parents=True, exist_ok=True)
        files: dict[str, Path] = {}

        midi = stem_dir / f"{name}.mid"
        writers.export_midi(shapes, midi, program=STEM_PROGRAM.get(name, 25))
        files["mid"] = midi

        txt = stem_dir / f"{name}.txt"
        writers.export_ascii(shapes, txt, cfg)
        files["txt"] = txt

        try:
            gp5 = stem_dir / f"{name}.gp5"
            writers.export_gp5(shapes, gp5, cfg, bpm=bpm, title=name, key=key)
            files["gp5"] = gp5
        except Exception as e:
            progress("export", f"{name}: gp5 failed to build ({e})")
        try:
            xml = stem_dir / f"{name}.musicxml"
            writers.export_musicxml(shapes, xml, bpm=bpm, key=key)
            files["musicxml"] = xml
        except Exception as e:
            progress("export", f"{name}: musicxml failed to build ({e})")

        results.append(StemResult(
            stem=name, bpm=bpm, key=key.name, note_count=len(notes),
            ascii_tab=render_ascii(shapes, cfg), files=files,
        ))
    return results
