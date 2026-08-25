"""
The single pipeline: file -> stems -> notes -> fingering -> output files.
Called by the CLI, the server, and the desktop app — the logic lives in one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .core.articulation import detect_legato_pairs
from .core.fretboard import TUNINGS, TabConfig, assign_tab, render_ascii
from .core.partition import split_lead_rhythm
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
    beats_per_measure: int = 4
    quantize_strength: float = 0.9
    separate: bool = True          # False = transcribe the whole mix
    split_guitars: bool = False    # split guitar into lead & rhythm parts


@dataclass(slots=True)
class StemResult:
    stem: str
    bpm: float
    key: str            # e.g. "F minor"
    note_count: int
    ascii_tab: str
    files: dict[str, Path] = field(default_factory=dict)  # ext -> path
    warnings: list[str] = field(default_factory=list)

    def to_dict(self, file_url) -> dict:
        """Wire form of the result; the pipeline owns this schema so a new
        field cannot be silently forgotten in the server. file_url maps
        (stem, path) -> download URL."""
        return {
            "stem": self.stem,
            "bpm": round(self.bpm, 1),
            "key": self.key,
            "notes": self.note_count,
            "ascii": self.ascii_tab,
            "warnings": list(self.warnings),
            "files": {ext: file_url(self.stem, p)
                      for ext, p in self.files.items()},
        }


def _noop(_stage: str, _msg: str) -> None:
    pass


def choose_tempo_source(stems: dict[str, Path], mix: Path,
                        is_audible: Callable[[Path], bool]) -> tuple[Path, str]:
    """Drums carry the clearest attacks — but only when they exist AND
    actually contain signal: htdemucs always writes a drums.wav, and for
    drumless material it is just residual bleed that would yield a garbage
    beat grid. Anything else falls back to the full mix."""
    drums = stems.get("drums")
    if drums is not None and is_audible(drums):
        return drums, "drums"
    return mix, "mix"


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
        # The tempo must be computed once or stems drift apart.
        tempo_source, source_name = choose_tempo_source(
            all_stems, audio, transcribe.stem_is_audible)
    else:
        stems = {"mix": audio}
        tempo_source, source_name = audio, "mix"

    progress("tempo", f"tempo and beat grid ({source_name})")
    bpm, beats, tempo_reliable = transcribe.detect_tempo(tempo_source)
    warnings: list[str] = []
    if not tempo_reliable:
        warnings.append("tempo: estimated poorly")
        progress("tempo",
                 f"tempo: estimated poorly, falling back to {bpm:.0f} BPM "
                 "without a beat grid")
    grid = Grid(beats, subdivision=opts.subdivision) if len(beats) > 1 else None

    # Key is track-global, so it comes from the full mix, not a stem.
    # It is also purely cosmetic (key signatures in the exports): a broken
    # detector must never take the whole job down with it.
    progress("tempo", "detecting the key")
    try:
        key = keydetect.detect_key(audio)
        progress("tempo", f"key: {key.name}")
    except Exception as e:  # noqa: BLE001 — degrade, don't die
        key = None
        warnings.append("key: detection failed")
        progress("tempo", f"key detection failed ({e}), "
                          "continuing without a key signature")

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

        parts = [(name, notes)]
        if opts.split_guitars and name == "guitar":
            split = split_lead_rhythm(notes)
            if split is not None:
                lead, rhythm = split
                parts = [("guitar_lead", lead), ("guitar_rhythm", rhythm)]
                progress("fingering",
                         f"guitar: split into lead ({len(lead)} notes) "
                         f"and rhythm ({len(rhythm)} notes)")
            else:
                progress("fingering", "guitar: no clear second part, not split")

        for part_name, part_notes in parts:
            progress("fingering", f"{part_name}: choosing the fingering")
            cfg = TabConfig(tuning=TUNINGS[STEM_TUNING.get(name, opts.tuning)])
            legato = detect_legato_pairs(part_notes)
            if legato:
                progress("fingering",
                         f"{part_name}: {len(legato)} legato pair(s) detected")
            shapes = assign_tab(part_notes, cfg, legato=legato)

            progress("export", f"{part_name}: writing files")
            stem_dir = out_dir / part_name
            stem_dir.mkdir(parents=True, exist_ok=True)
            files: dict[str, Path] = {}

            midi = stem_dir / f"{part_name}.mid"
            writers.export_midi(shapes, midi, program=STEM_PROGRAM.get(name, 25))
            files["mid"] = midi

            txt = stem_dir / f"{part_name}.txt"
            writers.export_ascii(shapes, txt, cfg, legato=legato)
            files["txt"] = txt

            try:
                gp5 = stem_dir / f"{part_name}.gp5"
                writers.export_gp5(shapes, gp5, cfg, bpm=bpm,
                                   beats_per_measure=opts.beats_per_measure,
                                   subdivision=opts.subdivision,
                                   title=part_name, key=key,
                                   origin=beats[0] if beats else 0.0,
                                   legato=legato)
                files["gp5"] = gp5
            except Exception as e:
                progress("export", f"{part_name}: gp5 failed to build ({e})")
            try:
                xml = stem_dir / f"{part_name}.musicxml"
                writers.export_musicxml(shapes, xml, bpm=bpm, key=key)
                files["musicxml"] = xml
            except Exception as e:
                progress("export", f"{part_name}: musicxml failed to build ({e})")

            results.append(StemResult(
                stem=part_name, bpm=bpm,
                key=key.name if key else "unknown key",
                note_count=len(part_notes),
                ascii_tab=render_ascii(shapes, cfg, legato=legato), files=files,
                warnings=list(warnings),
            ))
    return results
