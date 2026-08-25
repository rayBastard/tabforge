"""
The pipeline: file -> stems -> notes -> fingering -> output files.
Called by the CLI, the server, and the desktop app — the logic lives in
one place.

Two-step flow: run_analyze() separates the mix and produces per-stem
facts (does the instrument sound at all, its note range, a suggested
tuning) plus the shared tempo grid and key; the user then picks what to
transcribe and run_transcribe() works from the CACHED stems — demucs is
never run twice. run_pipeline() chains both for one-shot callers (CLI).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .core.articulation import detect_legato_pairs
from .core.fretboard import TUNINGS, TabConfig, assign_tab, render_ascii
from .core.instruments import profile_for
from .core.partition import split_lead_rhythm
from .core.quantize import Grid, gather_chords, quantize

ProgressFn = Callable[[str, str], None]  # (stage, message)

STAGES = ("separate", "analyze", "transcribe", "fingering", "export")

# stems that can carry pitched notes worth analyzing
PITCHED_STEMS = ("guitar", "bass", "piano", "vocals", "other")


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
class StemAnalysis:
    stem: str
    status: str                    # found | quiet | absent
    rms: float
    note_count: int = 0
    min_pitch: int | None = None
    max_pitch: int | None = None
    suggested_tuning: str | None = None

    def to_dict(self) -> dict:
        return {
            "stem": self.stem, "status": self.status,
            "rms": round(self.rms, 4), "notes": self.note_count,
            "min_pitch": self.min_pitch, "max_pitch": self.max_pitch,
            "suggested_tuning": self.suggested_tuning,
        }


@dataclass(slots=True)
class AnalyzeResult:
    stems: dict[str, Path]                 # every separated stem on disk
    analysis: dict[str, StemAnalysis]
    bpm: float
    beats: list[float]
    tempo_reliable: bool
    key: object | None                     # keydetect.Key
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class StemResult:
    stem: str
    bpm: float
    key: str            # e.g. "F minor"
    note_count: int
    ascii_tab: str
    files: dict[str, Path] = field(default_factory=dict)  # ext -> path
    warnings: list[str] = field(default_factory=list)
    tablature: bool = True     # False = notation-only instrument (piano)

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
            "tablature": self.tablature,
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


def suggest_tuning(stem: str, min_pitch: int | None) -> str | None:
    """Tuning suggestion from the lowest transcribed note.

    Guitar: E2 (40) and up fits standard; 39 wants Eb; 38 wants drop D;
    anything lower still maps to drop D (the closest we have) — the user
    can override. Bass: below E1 (28) suggests a 5-string.
    Non-string stems get no suggestion."""
    if min_pitch is None:
        return None
    if stem.startswith("guitar") or stem == "other":
        if min_pitch >= 40:
            return "standard"
        if min_pitch == 39:
            return "eb_standard"
        return "drop_d"
    if stem == "bass":
        return "bass_4" if min_pitch >= 28 else "bass_5"
    return None


def _quick_note_stats(wav: Path, stem: str, work_dir: Path,
                      sample_s: float = 45.0):
    """(note_count, min_pitch, max_pitch) from a slice around the middle
    of the stem — a full transcription of every stem would double the
    analyze stage; the middle of a song shows its real register."""
    import soundfile as sf

    from .audio import transcribe

    info = sf.info(str(wav))
    total_s = info.frames / info.samplerate
    if total_s > sample_s + 2:
        start = int(info.samplerate * (total_s - sample_s) / 2)
        frames = int(info.samplerate * sample_s)
        data, sr = sf.read(str(wav), start=start, frames=frames,
                           always_2d=True)
        clip = work_dir / f"_analyze_{stem}.wav"
        sf.write(str(clip), data, sr)
        target = clip
    else:
        target = wav
    notes = transcribe.transcribe_stem(
        target, **transcribe.PRESETS.get(stem, {}))
    notes = transcribe.cleanup(
        notes, max_polyphony=1 if stem == "bass" else 6)
    if not notes:
        return 0, None, None
    pitches = [n.pitch for n in notes]
    return len(notes), min(pitches), max(pitches)


RMS_FOUND = 0.005      # same threshold family as stem_is_audible
RMS_ABSENT = 0.002


def run_analyze(audio: Path, out_dir: Path,
                progress: ProgressFn = _noop) -> AnalyzeResult:
    """Separate + quick per-stem facts + shared tempo/key. No demucs work
    is ever repeated after this: the stems stay in out_dir/stems."""
    import numpy as np

    from .audio import keydetect, transcribe

    out_dir.mkdir(parents=True, exist_ok=True)
    progress("separate", "Separating into stems (first run downloads the model)")
    demucs_input = transcribe.ensure_decodable_wav(audio, out_dir)
    all_stems = transcribe.separate_stems(demucs_input, out_dir / "stems")

    warnings: list[str] = []

    progress("analyze", "tempo and beat grid")
    tempo_source, source_name = choose_tempo_source(
        all_stems, audio, transcribe.stem_is_audible)
    mix_data = transcribe.load_audio(audio) if tempo_source == audio else None
    bpm, beats, tempo_reliable = transcribe.detect_tempo(
        tempo_source, audio_data=mix_data)
    if not tempo_reliable:
        warnings.append("tempo: estimated poorly")
        progress("analyze",
                 f"tempo: estimated poorly, falling back to {bpm:.0f} BPM")
    else:
        progress("analyze", f"tempo: {bpm:.1f} BPM ({source_name})")

    try:
        key = keydetect.detect_key(audio, audio_data=mix_data)
        progress("analyze", f"key: {key.name}")
    except Exception as e:  # noqa: BLE001 — degrade, don't die
        key = None
        warnings.append("key: detection failed")
        progress("analyze", f"key detection failed ({e})")

    import librosa

    analysis: dict[str, StemAnalysis] = {}
    for stem in PITCHED_STEMS:
        wav = all_stems.get(stem)
        if wav is None:
            continue
        y, _sr = librosa.load(str(wav), mono=True)
        rms = float(np.sqrt(np.mean(y ** 2))) if len(y) else 0.0
        if rms < RMS_ABSENT:
            analysis[stem] = StemAnalysis(stem, "absent", rms)
            continue
        status = "found" if rms >= RMS_FOUND else "quiet"
        progress("analyze", f"{stem}: listening for its range")
        count, lo, hi = _quick_note_stats(wav, stem, out_dir)
        analysis[stem] = StemAnalysis(
            stem, status, rms, note_count=count,
            min_pitch=lo, max_pitch=hi,
            suggested_tuning=suggest_tuning(stem, lo))
        progress("analyze", f"{stem}: {status}, {count} notes in the sample")

    return AnalyzeResult(stems=all_stems, analysis=analysis,
                         bpm=bpm, beats=beats,
                         tempo_reliable=tempo_reliable, key=key,
                         warnings=warnings)


def run_transcribe(out_dir: Path, analyzed: AnalyzeResult,
                   opts: PipelineOptions,
                   progress: ProgressFn = _noop) -> list[StemResult]:
    """Transcribe the SELECTED stems using the cached separation and the
    shared grid/key from run_analyze."""
    from .audio import transcribe
    from .export import writers

    stems = {k: v for k, v in analyzed.stems.items() if k in opts.stems}

    # Everything the user did NOT pick becomes a play-along backing track.
    backing_dir = out_dir / "backing"
    backing_dir.mkdir(parents=True, exist_ok=True)
    if transcribe.mix_backing(analyzed.stems, opts.stems,
                              backing_dir / "backing.wav"):
        progress("transcribe", "backing track mixed from unselected stems")

    bpm, beats, key = analyzed.bpm, analyzed.beats, analyzed.key
    warnings = list(analyzed.warnings)
    grid = Grid(beats, subdivision=opts.subdivision) if len(beats) > 1 else None

    results: list[StemResult] = []
    for name, wav in stems.items():
        progress("transcribe", f"{name}: detecting notes")
        notes = transcribe.transcribe_stem(wav, **transcribe.PRESETS.get(name, {}))
        notes = transcribe.cleanup(
            notes, max_polyphony=1 if name == "bass" else 6)
        if not notes:
            progress("transcribe", f"{name}: no notes found, skipped")
            continue

        stem_profile = profile_for(name)
        if stem_profile.chord_gather_window > 0:
            notes = gather_chords(notes,
                                  window=stem_profile.chord_gather_window)
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
            profile = profile_for(part_name)
            tuning_key = profile.tuning or opts.tuning
            cfg = TabConfig(tuning=TUNINGS[tuning_key],
                            max_fret=profile.max_fret)
            legato = (detect_legato_pairs(part_notes)
                      if profile.wants_legato_pairs else [])
            if legato:
                progress("fingering",
                         f"{part_name}: {len(legato)} legato pair(s) detected")
            shapes = assign_tab(part_notes, cfg,
                                legato=legato if profile.allow_hammer else None)

            progress("export", f"{part_name}: writing files")
            stem_dir = out_dir / part_name
            stem_dir.mkdir(parents=True, exist_ok=True)
            files: dict[str, Path] = {}

            midi = stem_dir / f"{part_name}.mid"
            writers.export_midi(shapes, midi, program=profile.midi_program)
            files["mid"] = midi

            if profile.tablature:      # an ASCII tab makes no sense for keys
                txt = stem_dir / f"{part_name}.txt"
                writers.export_ascii(shapes, txt, cfg, legato=legato)
                files["txt"] = txt

            try:
                gp5 = stem_dir / f"{part_name}.gp5"
                writers.export_gp5(shapes, gp5, cfg, bpm=bpm,
                                   beats_per_measure=opts.beats_per_measure,
                                   subdivision=opts.subdivision,
                                   title=part_name, key=key,
                                   legato=legato, grid=grid,
                                   profile=profile)
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
                ascii_tab=(render_ascii(shapes, cfg, legato=legato)
                           if profile.tablature else ""),
                files=files,
                warnings=warnings,
                tablature=profile.tablature,
            ))
    return results


def _analyze_mix_only(audio: Path, opts: PipelineOptions,
                      progress: ProgressFn) -> AnalyzeResult:
    """separate=False path: the whole mix acts as the single 'stem'."""
    from .audio import keydetect, transcribe

    progress("analyze", "tempo and beat grid (mix)")
    mix_data = transcribe.load_audio(audio)
    bpm, beats, reliable = transcribe.detect_tempo(audio, audio_data=mix_data)
    warnings = [] if reliable else ["tempo: estimated poorly"]
    try:
        key = keydetect.detect_key(audio, audio_data=mix_data)
    except Exception:  # noqa: BLE001
        key = None
        warnings.append("key: detection failed")
    return AnalyzeResult(stems={"mix": audio}, analysis={},
                         bpm=bpm, beats=beats, tempo_reliable=reliable,
                         key=key, warnings=warnings)


def run_pipeline(audio: Path, out_dir: Path,
                 opts: PipelineOptions | None = None,
                 progress: ProgressFn = _noop) -> list[StemResult]:
    """One-shot analyze + transcribe (CLI and legacy callers)."""
    opts = opts or PipelineOptions()
    out_dir.mkdir(parents=True, exist_ok=True)
    if opts.separate:
        analyzed = run_analyze(audio, out_dir, progress)
    else:
        analyzed = _analyze_mix_only(audio, opts, progress)
    return run_transcribe(out_dir, analyzed, opts, progress)
