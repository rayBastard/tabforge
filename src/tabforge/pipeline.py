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
from .core.fretboard import (NoteEvent, TUNINGS, TabConfig, assign_tab,
                             render_ascii)
from .core.instruments import profile_for
from .core.partition import split_hands, split_lead_rhythm
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
    # per-stem role override, e.g. {"guitar": "piano"} when the "guitar"
    # stem actually holds an orchestral line and deserves notation
    treat: dict = field(default_factory=dict)


@dataclass(slots=True)
class StemAnalysis:
    stem: str
    status: str                    # found | quiet | absent
    rms: float
    note_count: int = 0
    min_pitch: int | None = None
    max_pitch: int | None = None
    suggested_tuning: str | None = None
    # what the tagger actually HEARS in the stem (demucs names outputs
    # by role, not by listening — an orchestra lands in "guitar")
    sounds_like: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "stem": self.stem, "status": self.status,
            "rms": round(self.rms, 4), "notes": self.note_count,
            "min_pitch": self.min_pitch, "max_pitch": self.max_pitch,
            "suggested_tuning": self.suggested_tuning,
            "sounds_like": list(self.sounds_like),
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


# the tuning ladder for guitars: the highest tuning whose lowest string
# still reaches the part's lowest note; drop shapes win in the middle
# (that's how downtuned rhythm parts are actually played), and from
# drop A down a 7-string is the usual instrument
_GUITAR_LADDER = (
    (40, "standard"), (39, "eb_standard"), (38, "drop_d"),
    (37, "drop_db"), (36, "drop_c"), (35, "drop_b"),
    (34, "drop_bb"), (33, "seven_drop_a"),
)


def suggest_tuning(stem: str, min_pitch: int | None) -> str | None:
    """Tuning suggestion from the lowest transcribed note.
    Bass: below E1 (28) suggests a 5-string. Non-string stems get no
    suggestion."""
    if min_pitch is None:
        return None
    if stem.startswith("guitar"):
        for low, name in _GUITAR_LADDER:
            if min_pitch >= low:
                return name
        return "seven_drop_a"     # the lowest gp5 can express
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
        notes, max_polyphony=1 if stem == "bass"
        else 10 if stem == "piano" else 6)
    if not notes:
        return 0, None, None
    # ROBUST range: a real lowest note is a note the part actually
    # PLAYS — the riff hammers its root at one pitch again and again
    # (drop-A material: A1 dozens of times), while transcription ghosts
    # and bleed scatter across many pitches with a few hits each. The
    # extreme pitches must individually repeat: at least 1% of the
    # notes (min 3) AT that exact pitch.
    from collections import Counter
    hist = Counter(n.pitch for n in notes)
    need = max(3, len(notes) // 100)

    def robust(ordered) -> int:
        for p in ordered:
            if hist[p] >= need:
                return p
        return ordered[0]

    pitches = sorted(hist)
    return len(notes), robust(pitches), robust(pitches[::-1])


RMS_FOUND = 0.005      # same threshold family as stem_is_audible
RMS_ABSENT = 0.002


def run_analyze(audio: Path, out_dir: Path,
                progress: ProgressFn = _noop,
                cancel_token: object | None = None) -> AnalyzeResult:
    """Separate + quick per-stem facts + shared tempo/key. No demucs work
    is ever repeated after this: the stems stay in out_dir/stems.

    cancel_token lets a caller abort the demucs subprocess mid-run via
    transcribe.abort_separation(token); cooperative cancellation between
    stages happens by raising from the progress callback."""
    import numpy as np

    from .audio import keydetect, transcribe

    out_dir.mkdir(parents=True, exist_ok=True)
    progress("separate", "Separating into stems (first run downloads the model)")
    demucs_input = transcribe.ensure_decodable_wav(audio, out_dir)
    all_stems = transcribe.separate_stems(demucs_input, out_dir / "stems",
                                          cancel_token=cancel_token)

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

    import os as _os

    from .audio import tagging
    if (not tagging._CHECKPOINT.exists()
            and not _os.environ.get("TABFORGE_NO_TAGGING")):
        progress("analyze",
                 "downloading the instrument tagger (~320 MB, first run only)")

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
        from .audio.tagging import tag_stem
        heard = tag_stem(wav)
        analysis[stem] = StemAnalysis(
            stem, status, rms, note_count=count,
            min_pitch=lo, max_pitch=hi,
            suggested_tuning=suggest_tuning(stem, lo),
            sounds_like=heard)
        progress("analyze",
                 f"{stem}: {status}, {count} notes in the sample"
                 + (f" — sounds like {heard[0]}" if heard else ""))

    # drums are unpitched: no note range, no tuning — just "does the kit
    # sound at all" and roughly how busy it is
    drums_wav = all_stems.get("drums")
    if drums_wav is not None:
        y, sr = librosa.load(str(drums_wav), mono=True)
        rms = float(np.sqrt(np.mean(y ** 2))) if len(y) else 0.0
        if rms < RMS_ABSENT:
            analysis["drums"] = StemAnalysis("drums", "absent", rms)
        else:
            status = "found" if rms >= RMS_FOUND else "quiet"
            onsets = librosa.onset.onset_detect(y=y, sr=sr)
            analysis["drums"] = StemAnalysis(
                "drums", status, rms, note_count=len(onsets))
            progress("analyze", f"drums: {status}, {len(onsets)} hits")

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

    # demucs emits drums first; a score reads melodic-top, drums-bottom
    part_order = {name: i for i, name in enumerate((*PITCHED_STEMS, "drums"))}
    stems = dict(sorted(
        ((k, v) for k, v in analyzed.stems.items() if k in opts.stems),
        key=lambda kv: part_order.get(kv[0], len(part_order))))

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
    song_parts: list = []          # writers.SongPart, one per produced part
    for name, wav in stems.items():
        if name == "drums":
            result = _transcribe_drums_part(out_dir, wav, analyzed, opts,
                                            grid, warnings, song_parts,
                                            progress)
            if result is not None:
                results.append(result)
            continue
        progress("transcribe", f"{name}: detecting notes")
        notes = transcribe.transcribe_stem(wav, **transcribe.PRESETS.get(name, {}))
        # ten fingers: a cap of 6 voices silently dropped the QUIETEST
        # notes of dense piano writing — the high melody first
        notes = transcribe.cleanup(
            notes, max_polyphony=1 if name == "bass"
            else 10 if name == "piano" else 6)
        if not notes:
            progress("transcribe", f"{name}: no notes found, skipped")
            continue

        # a stem's ROLE decides how it is written. "other" is whatever
        # demucs could not name — strings, synths, brass — and none of
        # that has frets: it always reads as notation (keys profile).
        # opts.treat can still overrule per stem (CLI / saved projects).
        def role_of(nm: str) -> str:
            default = "piano" if nm == "other" else nm
            return opts.treat.get(nm, default)

        stem_profile = profile_for(role_of(name))
        if stem_profile.chord_gather_window > 0:
            notes = gather_chords(notes,
                                  window=stem_profile.chord_gather_window)
        if grid is not None:
            notes = quantize(notes, grid, strength=opts.quantize_strength)

        parts = [(name, notes)]
        if name == "piano" and role_of(name) == "piano":
            # a grand staff is two tracks: right hand (treble) and left
            # hand (bass); the frontend renders them as one Keys view
            hands = split_hands(notes)
            if hands is not None:
                right, left = hands
                parts = [("piano", right), ("piano_left", left)]
                progress("fingering",
                         f"piano: grand staff — {len(right)} right-hand "
                         f"and {len(left)} left-hand notes")
        if name == "guitar" and role_of(name) == "guitar":
            # ALWAYS look for a second guitar part: the detector itself
            # says whether the material really carries two (its "no
            # clear second part" answer keeps single guitars whole)
            split = split_lead_rhythm(notes)
            if split is not None:
                lead, rhythm = split
                parts = [("guitar_lead", lead), ("guitar_rhythm", rhythm)]
                progress("fingering",
                         f"guitar: two parts detected — lead "
                         f"({len(lead)} notes) and rhythm "
                         f"({len(rhythm)} notes)")

        for part_name, part_notes in parts:
            progress("fingering", f"{part_name}: choosing the fingering")
            profile = profile_for(role_of(part_name))
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

            song_parts.append(writers.SongPart(
                name=part_name, shapes=shapes, cfg=cfg,
                profile=profile, legato=legato))
            _save_part_state(out_dir, part_name, part_notes, legato,
                             tuning_key, profile.name)
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

    # the whole project as ONE multi-track score: the unified player
    # plays it together, with per-track mute/solo
    if song_parts:
        progress("export", "assembling the multi-track project score")
        song_dir = out_dir / "song"
        song_dir.mkdir(parents=True, exist_ok=True)
        try:
            writers.export_song_gp5(
                song_parts, song_dir / "song.gp5",
                bpm=bpm, beats_per_measure=opts.beats_per_measure,
                subdivision=opts.subdivision, title="TabForge project",
                key=key, grid=grid)
        except Exception as e:
            progress("export", f"project score failed to build ({e})")
    return results


def _transcribe_drums_part(out_dir: Path, wav: Path,
                           analyzed: AnalyzeResult, opts: PipelineOptions,
                           grid, warnings: list[str], song_parts: list,
                           progress: ProgressFn) -> StemResult | None:
    """The percussion branch of run_transcribe: onsets instead of
    Basic Pitch, kit voices instead of a fretboard — so no fingering
    search, no pins, and no parts.json entry (nothing to re-pin)."""
    from .audio import drums as drum_mod
    from .export import writers

    progress("transcribe", "drums: detecting hits")
    hits = drum_mod.transcribe_drums(wav)
    if not hits:
        progress("transcribe", "drums: no hits found, skipped")
        return None
    if grid is not None:
        hits = quantize(hits, grid, strength=opts.quantize_strength)

    profile = profile_for("drums")
    cfg = TabConfig(tuning=TUNINGS["percussion"], max_fret=profile.max_fret)
    shapes = drum_mod.drum_shapes(hits)
    ascii_grid = drum_mod.render_drum_ascii(shapes)

    progress("export", "drums: writing files")
    stem_dir = out_dir / "drums"
    stem_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}

    midi = stem_dir / "drums.mid"
    writers.export_midi(shapes, midi, program=0, is_drum=True)
    files["mid"] = midi
    txt = stem_dir / "drums.txt"
    txt.write_text(ascii_grid, encoding="utf-8")
    files["txt"] = txt
    try:
        gp5 = stem_dir / "drums.gp5"
        writers.export_gp5(shapes, gp5, cfg, bpm=analyzed.bpm,
                           beats_per_measure=opts.beats_per_measure,
                           subdivision=opts.subdivision,
                           title="drums", key=analyzed.key,
                           grid=grid, profile=profile)
        files["gp5"] = gp5
    except Exception as e:
        progress("export", f"drums: gp5 failed to build ({e})")

    song_parts.append(writers.SongPart(
        name="drums", shapes=shapes, cfg=cfg, profile=profile))
    return StemResult(
        stem="drums", bpm=analyzed.bpm,
        key=analyzed.key.name if analyzed.key else "unknown key",
        note_count=len(hits), ascii_tab=ascii_grid,
        files=files, warnings=list(warnings), tablature=False)


# ---------------------------------------------------------------------------
# The note editor: parts are persisted as JSON so a pin can re-run the
# fingering search for one instrument without touching the audio again.
# ---------------------------------------------------------------------------

def _parts_file(out_dir: Path) -> Path:
    return out_dir / "parts.json"


def _save_part_state(out_dir: Path, part_name: str, notes, legato,
                     tuning_key: str, profile_name: str) -> None:
    import json

    path = _parts_file(out_dir)
    state = json.loads(path.read_text()) if path.exists() else {}
    index_of = {id(n): i for i, n in enumerate(notes)}
    state[part_name] = {
        "notes": [{"pitch": n.pitch, "start": n.start,
                   "duration": n.duration, "velocity": n.velocity,
                   "bends": list(n.bends)} for n in notes],
        "legato": [[index_of[id(a)], index_of[id(b)], kind]
                   for a, b, kind in (legato or [])
                   if id(a) in index_of and id(b) in index_of],
        "tuning": tuning_key,
        "profile": profile_name,
        "pins": {},
    }
    path.write_text(json.dumps(state))


def apply_repin(out_dir: Path, part_name: str, tick: int, pitch: int,
                string: int | None, shared: AnalyzeResult,
                opts: PipelineOptions) -> dict:
    """Pin (or unpin, string=None) a note of one part and rebuild its
    files plus the multi-track song.gp5. Returns {'prev': old_pin,
    'ascii': new_ascii} — raises ValueError when the note isn't found."""
    import json

    from .export import writers

    path = _parts_file(out_dir)
    state = json.loads(path.read_text())
    if part_name not in state:
        raise ValueError(f"unknown part: {part_name}")

    grid = (Grid(shared.beats, subdivision=opts.subdivision)
            if len(shared.beats) > 1 else None)

    def revive(part):
        return [NoteEvent(n["pitch"], n["start"], n["duration"],
                          n["velocity"], list(n["bends"]))
                for n in part["notes"]]

    # locate the clicked note: same tick (the collision shift allows ±1)
    # and the same pitch
    part = state[part_name]
    notes = revive(part)
    target = None
    for i, n in enumerate(notes):
        if n.pitch != pitch:
            continue
        t = grid.tick_index(n.start) if grid else round(
            n.start / (60.0 / shared.bpm / opts.subdivision))
        if abs(t - tick) <= 1:
            target = i
            break
    if target is None:
        raise ValueError("note not found at that position")

    pins = {int(k): v for k, v in part["pins"].items()}
    prev = pins.get(target)
    if string is None:
        pins.pop(target, None)
    else:
        pins[target] = int(string)
    part["pins"] = {str(k): v for k, v in pins.items()}
    path.write_text(json.dumps(state))

    # rebuild every part's shapes (cheap — pure math), rewrite the edited
    # part's files and the shared song.gp5
    song_parts = []
    edited_ascii = ""
    for name, p in state.items():
        p_notes = revive(p)
        profile = profile_for(p["profile"])
        cfg = TabConfig(tuning=TUNINGS[p["tuning"]],
                        max_fret=profile.max_fret)
        p_legato = [(p_notes[a], p_notes[b], kind)
                    for a, b, kind in p["legato"]]
        p_pins = {int(k): v for k, v in p["pins"].items()}
        shapes = assign_tab(p_notes, cfg,
                            legato=p_legato if profile.allow_hammer else None,
                            pins=p_pins or None)
        song_parts.append(writers.SongPart(
            name=name, shapes=shapes, cfg=cfg,
            profile=profile, legato=p_legato))
        if name == part_name:
            stem_dir = out_dir / name
            writers.export_gp5(shapes, stem_dir / f"{name}.gp5", cfg,
                               bpm=shared.bpm,
                               beats_per_measure=opts.beats_per_measure,
                               subdivision=opts.subdivision,
                               title=name, key=shared.key,
                               legato=p_legato, grid=grid, profile=profile)
            writers.export_midi(shapes, stem_dir / f"{name}.mid",
                                program=profile.midi_program)
            if profile.tablature:
                edited_ascii = render_ascii(shapes, cfg, legato=p_legato)
                writers.export_ascii(shapes, stem_dir / f"{name}.txt",
                                     cfg, legato=p_legato)

    writers.export_song_gp5(song_parts, out_dir / "song" / "song.gp5",
                            bpm=shared.bpm,
                            beats_per_measure=opts.beats_per_measure,
                            subdivision=opts.subdivision,
                            title="TabForge project", key=shared.key,
                            grid=grid)
    return {"prev": prev, "ascii": edited_ascii}


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
