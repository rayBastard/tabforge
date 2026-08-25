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

STAGES = ("separate", "transcribe", "tempo", "fingering", "export")


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
    note_count: int
    ascii_tab: str
    files: dict[str, Path] = field(default_factory=dict)  # ext -> path


def _noop(_stage: str, _msg: str) -> None:
    pass


def run_pipeline(audio: Path, out_dir: Path,
                 opts: PipelineOptions | None = None,
                 progress: ProgressFn = _noop) -> list[StemResult]:
    from .audio import transcribe
    from .export import writers

    opts = opts or PipelineOptions()
    out_dir.mkdir(parents=True, exist_ok=True)

    if opts.separate:
        progress("separate", "Separating into stems (first run downloads the model)")
        stems = transcribe.separate_stems(audio, out_dir / "stems")
        stems = {k: v for k, v in stems.items() if k in opts.stems}
    else:
        stems = {"mix": audio}

    results: list[StemResult] = []
    for name, wav in stems.items():
        progress("transcribe", f"{name}: detecting notes")
        notes = transcribe.transcribe_stem(wav, **transcribe.PRESETS.get(name, {}))
        notes = transcribe.cleanup(
            notes, max_polyphony=1 if name == "bass" else 6)
        if not notes:
            progress("transcribe", f"{name}: no notes found, skipped")
            continue

        progress("tempo", f"{name}: tempo and beat grid")
        bpm, beats = transcribe.detect_tempo(wav)
        if len(beats) > 1:
            grid = Grid(beats, subdivision=opts.subdivision)
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
            writers.export_gp5(shapes, gp5, cfg, bpm=bpm, title=name)
            files["gp5"] = gp5
        except Exception as e:
            progress("export", f"{name}: gp5 failed to build ({e})")
        try:
            xml = stem_dir / f"{name}.musicxml"
            writers.export_musicxml(shapes, xml, bpm=bpm)
            files["musicxml"] = xml
        except Exception as e:
            progress("export", f"{name}: musicxml failed to build ({e})")

        results.append(StemResult(
            stem=name, bpm=bpm, note_count=len(notes),
            ascii_tab=render_ascii(shapes, cfg), files=files,
        ))
    return results
