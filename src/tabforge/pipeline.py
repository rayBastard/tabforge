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
    # 0.0 = keep truthful onsets (task 56): the gp5 writer slots notes
    # onto the grid at export anyway, so a pre-snap adds NOTHING to the
    # notation while destroying timing everywhere else — measured on
    # golden: piano 0.27->0.44, Loken bass 0.41->0.61, guitar +0.01.
    # Partial strengths are the worst of both worlds (notes land in
    # no-man's land between raw and grid).
    quantize_strength: float = 0.0
    separate: bool = True          # False = transcribe the whole mix
    split_guitars: bool = False    # split guitar into lead & rhythm parts
    # per-stem role override, e.g. {"guitar": "piano"} when the "guitar"
    # stem actually holds an orchestral line and deserves notation
    treat: dict = field(default_factory=dict)
    # separation backend: "demucs" (default) or "roformer" (BS-Roformer-SW)
    separator: str = "demucs"
    # harmonic leak validation: drop a note when another stem holds
    # more than leak_margin times its harmonic energy (0 = off)
    leak_margin: float = 2.0
    # tempo octave: 0.5 halves the detected grid (152 -> 76), 2.0
    # doubles it. No audio discriminator survived measurement (dense
    # eighth textures defeat beat-strength alternation), so the octave
    # is the USER's call — they know the piece.
    tempo_scale: float = 1.0
    # low-register octave double-pass for bass/guitar/vocals.
    # Default OFF: on the stand it made bass WORSE (0.15 -> 0.07 mean
    # F1) and left guitar flat — the low-register misery there is
    # separation mush, not Basic Pitch's frequency resolution. The
    # machinery stays for re-testing on real golden fragments.
    low_pass: bool = False


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
    # MT3 arbiter's opinion: found | absent | uncertain | None (no MT3)
    verdict: str | None = None
    # median strong-attack inter-onset interval, s (half-time detector)
    median_ioi: float | None = None

    def to_dict(self) -> dict:
        return {
            "stem": self.stem, "status": self.status,
            "rms": round(self.rms, 4), "notes": self.note_count,
            "min_pitch": self.min_pitch, "max_pitch": self.max_pitch,
            "suggested_tuning": self.suggested_tuning,
            "sounds_like": list(self.sounds_like),
            "verdict": self.verdict,
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


def _beatworthy(wav: Path, crest_min: float = 15.0) -> bool:
    """A REAL kit's onset envelope is spiky (99th percentile 43-59x its
    median on golden); a phantom drums stem — bleed hiss on drumless
    material — is nearly flat (5.5x on Fulgrim, which quietly fed the
    beat tracker garbage and doubled the tempo). Task 56."""
    import librosa
    import numpy as np

    y, sr = librosa.load(str(wav), mono=True, duration=90)
    if not len(y):
        return False
    oenv = librosa.onset.onset_strength(y=y, sr=sr)
    positive = oenv[oenv > 0]
    if not len(positive):
        return False
    crest = float(np.percentile(oenv, 99) / max(np.median(positive), 1e-6))
    return crest >= crest_min


def choose_tempo_source(stems: dict[str, Path], mix: Path,
                        is_audible: Callable[[Path], bool]) -> tuple[Path, str]:
    """Drums carry the clearest attacks — but only when they exist AND
    actually contain a kit: htdemucs always writes a drums.wav, and for
    drumless material it is just residual bleed that would yield a
    garbage beat grid (RMS alone cannot tell — the crest test can).
    Anything else falls back to the full mix."""
    drums = stems.get("drums")
    if drums is not None and is_audible(drums) and _beatworthy(drums):
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
        return 0, None, None, None
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

    # median inter-onset interval of the STRONG attacks (chord notes
    # within 50 ms merge into one onset) — the half-time detector's
    # raw material (task 56): weak BP re-attacks would drown it
    import statistics
    vmed = statistics.median(n.velocity for n in notes)
    onsets: list[float] = []
    for n in sorted(notes, key=lambda n: n.start):
        if n.velocity >= vmed and (not onsets
                                   or n.start - onsets[-1] > 0.05):
            onsets.append(n.start)
    iois = [b - a for a, b in zip(onsets, onsets[1:])]
    ioi = statistics.median(iois) if len(iois) >= 10 else None

    return len(notes), robust(pitches), robust(pitches[::-1]), ioi


RMS_FOUND = 0.005      # same threshold family as stem_is_audible
RMS_ABSENT = 0.002


def run_analyze(audio: Path, out_dir: Path,
                progress: ProgressFn = _noop,
                cancel_token: object | None = None,
                separator: str = "demucs",
                use_mt3: bool = True) -> AnalyzeResult:
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
    all_stems = transcribe.separate(demucs_input, out_dir / "stems",
                                    backend=separator,
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
        count, lo, hi, ioi = _quick_note_stats(wav, stem, out_dir)
        from .audio.tagging import tag_stem
        heard = tag_stem(wav)
        analysis[stem] = StemAnalysis(
            stem, status, rms, note_count=count, median_ioi=ioi,
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

    # MT3 arbiter (task 54): optional second opinion on WHAT actually
    # plays — verdicts refine the RMS statuses on the cards. Activates
    # only when a YourMT3+ install is configured (TABFORGE_MT3_DIR).
    try:
        from .audio import arbiter
        if use_mt3 and arbiter.find_mt3() is not None:
            import soundfile as sf
            info = sf.info(str(demucs_input))
            duration_min = info.frames / info.samplerate / 60
            v = arbiter.verdicts(
                demucs_input, all_stems,
                {s: a.status for s, a in analysis.items()},
                duration_min, out_dir, progress)
            if v:
                for stem, verdict in v.items():
                    if stem in analysis:
                        analysis[stem].verdict = verdict
    except Exception:  # noqa: BLE001 — the arbiter must never kill analyze
        progress("analyze", "MT3 arbiter failed — cards keep their "
                            "RMS-based statuses")

    # MuScriptor whole-mix transcription (task 57): cached once here —
    # bass and guitar route to it when the user has an install
    # (weights are CC BY-NC, never bundled; silently absent otherwise)
    try:
        from .audio.muscriptor import find_muscriptor, run_muscriptor
        if find_muscriptor() is not None:
            run_muscriptor(demucs_input, out_dir, progress)
    except Exception:  # noqa: BLE001 — optional backend, never fatal
        progress("analyze", "MuScriptor failed — stem transcription "
                            "will keep its instruments")

    # Half-time detector (task 56): on DRUMLESS keys-led material the
    # beat tracker loves double time (Fulgrim: 152 vs the sheet's 76).
    # When the confirmed lead keys move at the BEAT rate of the chosen
    # grid — nothing between beats — the musical tempo is half. Only
    # fires when no kit dictates the grid; the UI selector still lets
    # the user override either way.
    piano = analysis.get("piano")
    keys_led = (piano is not None and piano.status == "found"
                and piano.verdict in (None, "found", "uncertain")
                and source_name == "mix")   # no kit passed the crest gate
    if (keys_led and len(beats) > 3 and piano.median_ioi
            and piano.median_ioi >= 0.9 * (60.0 / bpm)):
        bpm /= 2
        beats = beats[::2]
        progress("analyze",
                 f"tempo: keys move at the beat rate — half time "
                 f"({bpm:.0f} BPM) suggested")

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
    if opts.tempo_scale != 1.0:
        beats = scale_beats(beats, opts.tempo_scale)
        bpm = bpm * opts.tempo_scale
    warnings = list(analyzed.warnings)
    grid = Grid(beats, subdivision=opts.subdivision) if len(beats) > 1 else None

    # shared spectrogram cache for the leak filter (one STFT per stem)
    from .audio.validate import _StemSpectra, filter_leaked_notes
    spectra = (_StemSpectra(analyzed.stems)
               if opts.leak_margin > 0 else None)

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
        preset = transcribe.PRESETS.get(name, {})
        # note_source routing (task 57): an instrument whose profile
        # trusts a whole-mix model takes its notes from that model's
        # cached MIDI instead of the separated stem — only for the
        # stem that IS that card (an "other" stem treated as keys must
        # not duplicate the piano's notes). Missing cache = silent
        # fallback to the stem path.
        from_mt3 = False
        src_profile = profile_for(opts.treat.get(name, name))
        source = src_profile.note_source
        if (source in ("mt3", "muscriptor")
                and opts.treat.get(name, name) == name):
            from .audio.arbiter import mt3_card_notes
            cache = out_dir / f"{source}.mid"
            mix_notes = mt3_card_notes(cache, name)
            if mix_notes:
                progress("transcribe",
                         f"{name}: {len(mix_notes)} notes from the "
                         f"whole-mix {source} transcription")
                notes = mix_notes
                from_mt3 = True
        if from_mt3:
            pass
        elif src_profile.transcriber == "mono":
            # monophonic stems (bass, recitative vocals): a mono f0
            # tracker cannot produce octave twins — task 53. But a
            # dirty stem (heavy bleed) defeats the tracker: when it
            # locks onto far fewer events than Basic Pitch hears, the
            # stem is not mono-clean and BP keeps the job. Threshold
            # 0.4 set on the golden corpus (Hero 0.21 / Loken 0.65) —
            # provisional until more references arrive.
            from .audio.mono import MONO_PRESETS, transcribe_mono
            mono_notes = transcribe_mono(wav, **MONO_PRESETS.get(name, {}))
            bp_notes = transcribe.cleanup(
                transcribe.transcribe_stem(wav, **preset),
                max_polyphony=1 if name == "bass" else 6)
            if len(mono_notes) >= 0.4 * max(len(bp_notes), 1):
                progress("transcribe", f"{name}: monophonic f0 path "
                                       f"({len(mono_notes)} notes)")
                notes = mono_notes
            else:
                progress("transcribe",
                         f"{name}: stem too dense for the mono tracker "
                         f"({len(mono_notes)} vs {len(bp_notes)}), "
                         f"Basic Pitch keeps it")
                notes = bp_notes
        elif opts.low_pass and name in ("bass", "guitar", "vocals"):
            # the low register reads badly at native pitch — a second,
            # octave-shifted pass owns everything below ~A2
            from .audio.lowregister import transcribe_with_low_pass
            notes = transcribe_with_low_pass(wav, preset)
        else:
            notes = transcribe.transcribe_stem(wav, **preset)
        # ten fingers: a cap of 6 voices silently dropped the QUIETEST
        # notes of dense piano writing — the high melody first
        notes = transcribe.cleanup(
            notes, max_polyphony=1 if name == "bass"
            else 10 if name == "piano" else 6)
        # Validate only the CATCH-BASIN stems (other/vocals/piano):
        # that is where everyone else's bleed collects as junk notes.
        # Guitar and bass are exempt — their own stems separate weakly,
        # so their true notes' energy often sits elsewhere and the
        # filter would slaughter real lines (measured on the stand).
        if (spectra is not None and not from_mt3
                and name in ("piano", "vocals", "other")):
            # mt3-sourced notes are exempt: they come from the MIX, so
            # judging them by where demucs happened to put the energy
            # would re-import the separation's diseases.
            # dead notes are exempt: a spoken syllable is not harmonic
            # at its (placeholder) pitch, but it IS a real vocal event
            pitched = [n for n in notes if not n.dead]
            kept = filter_leaked_notes(pitched, name, analyzed.stems,
                                       spectra, margin=opts.leak_margin)
            if len(kept) < len(pitched):
                progress("transcribe",
                         f"{name}: {len(pitched) - len(kept)} leaked "
                         f"note(s) filtered out")
            notes = sorted(kept + [n for n in notes if n.dead],
                           key=lambda n: n.start)
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
            from .audio.validate import note_confidences
            _save_part_state(out_dir, part_name, part_notes, legato,
                             tuning_key, profile.name,
                             # mix-sourced notes must not be judged by
                             # the stem's spectrum (see the leak-filter
                             # exemption above)
                             conf=note_confidences(
                                 part_notes, name, analyzed.stems,
                                 None if from_mt3 else spectra))
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

def scale_beats(beats: list[float], scale: float) -> list[float]:
    """The tempo-octave correction: scale=0.5 keeps every second beat
    (a 152 grid becomes the 76 one), scale=2.0 inserts midpoints."""
    if len(beats) < 2 or scale == 1.0:
        return list(beats)
    if scale == 0.5:
        kept = beats[0::2]
        return kept if len(kept) >= 2 else list(beats)
    if scale == 2.0:
        out: list[float] = []
        for a, b in zip(beats, beats[1:]):
            out += [a, (a + b) / 2]
        out.append(beats[-1])
        return out
    raise ValueError(f"unsupported tempo scale: {scale}")


def _parts_file(out_dir: Path) -> Path:
    return out_dir / "parts.json"


def _save_part_state(out_dir: Path, part_name: str, notes, legato,
                     tuning_key: str, profile_name: str,
                     conf: list[float] | None = None) -> None:
    import json

    path = _parts_file(out_dir)
    state = json.loads(path.read_text()) if path.exists() else {}
    index_of = {id(n): i for i, n in enumerate(notes)}
    conf = conf or [1.0] * len(notes)
    state[part_name] = {
        "notes": [{"pitch": n.pitch, "start": n.start,
                   "duration": n.duration, "velocity": n.velocity,
                   "bends": list(n.bends), "dead": n.dead,
                   "conf": c}
                  for n, c in zip(notes, conf)],
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

    beats = (scale_beats(shared.beats, opts.tempo_scale)
             if opts.tempo_scale != 1.0 else shared.beats)
    grid = (Grid(beats, subdivision=opts.subdivision)
            if len(beats) > 1 else None)

    def revive(part):
        return [NoteEvent(n["pitch"], n["start"], n["duration"],
                          n["velocity"], list(n["bends"]),
                          n.get("dead", False))
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
            n.start / (60.0 / (shared.bpm * opts.tempo_scale)
                       / opts.subdivision))
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

    edited_ascii = _rebuild_outputs(out_dir, state, {part_name},
                                    shared, opts, grid)
    return {"prev": prev, "ascii": edited_ascii.get(part_name, "")}


def _revive_notes(part: dict) -> list[NoteEvent]:
    return [NoteEvent(n["pitch"], n["start"], n["duration"],
                      n["velocity"], list(n["bends"]),
                      n.get("dead", False))
            for n in part["notes"]]


def _rebuild_outputs(out_dir: Path, state: dict, edited: set[str],
                     shared: AnalyzeResult, opts: PipelineOptions,
                     grid) -> dict[str, str]:
    """Rebuild every part's shapes (cheap — pure math), rewrite the
    edited parts' files and the shared song.gp5. Returns the new ascii
    tab per edited part (empty string for notation-only parts)."""
    from .export import writers

    song_parts = []
    ascii_out: dict[str, str] = {}
    for name, p in state.items():
        p_notes = _revive_notes(p)
        if not p_notes:
            continue
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
        if name in edited:
            stem_dir = out_dir / name
            stem_dir.mkdir(parents=True, exist_ok=True)
            writers.export_gp5(shapes, stem_dir / f"{name}.gp5", cfg,
                               bpm=shared.bpm * opts.tempo_scale,
                               beats_per_measure=opts.beats_per_measure,
                               subdivision=opts.subdivision,
                               title=name, key=shared.key,
                               legato=p_legato, grid=grid, profile=profile)
            writers.export_midi(shapes, stem_dir / f"{name}.mid",
                                program=profile.midi_program)
            ascii_out[name] = ""
            if profile.tablature:
                ascii_out[name] = render_ascii(shapes, cfg,
                                               legato=p_legato)
                writers.export_ascii(shapes, stem_dir / f"{name}.txt",
                                     cfg, legato=p_legato)

    writers.export_song_gp5(song_parts, out_dir / "song" / "song.gp5",
                            bpm=shared.bpm * opts.tempo_scale,
                            beats_per_measure=opts.beats_per_measure,
                            subdivision=opts.subdivision,
                            title="TabForge project", key=shared.key,
                            grid=grid)
    return ascii_out


def _drop_notes(part: dict, removed: set[int]) -> list[dict]:
    """Remove notes by index; remap pins and legato pairs (both are
    INDEX-keyed) onto the surviving order. Returns the removed dicts."""
    keep = [i for i in range(len(part["notes"])) if i not in removed]
    remap = {old: new for new, old in enumerate(keep)}
    dropped = [part["notes"][i] for i in sorted(removed)]
    part["notes"] = [part["notes"][i] for i in keep]
    part["pins"] = {str(remap[int(k)]): v
                    for k, v in part["pins"].items() if int(k) in remap}
    part["legato"] = [[remap[a], remap[b], kind]
                      for a, b, kind in part["legato"]
                      if a in remap and b in remap]
    return dropped


def _insert_notes(part: dict, new_notes: list[dict]) -> None:
    """Merge foreign notes into a part keeping time order; pins and
    legato indices follow their notes through the re-sort."""
    combined = ([(n, i) for i, n in enumerate(part["notes"])]
                + [(n, None) for n in new_notes])
    combined.sort(key=lambda t: t[0]["start"])
    remap = {old: new for new, (_, old) in enumerate(combined)
             if old is not None}
    part["notes"] = [n for n, _ in combined]
    part["pins"] = {str(remap[int(k)]): v
                    for k, v in part["pins"].items() if int(k) in remap}
    part["legato"] = [[remap[a], remap[b], kind]
                      for a, b, kind in part["legato"]]


BULK_OPS = ("octave_up", "octave_down", "delete", "dedup_octaves",
            "reassign")


def apply_bulk_edit(out_dir: Path, part_name: str, start_tick: int,
                    end_tick: int, op: str, shared: AnalyzeResult,
                    opts: PipelineOptions,
                    target_part: str | None = None) -> dict:
    """Mass editor operation (task 55) on every note of a part whose
    grid tick falls in [start_tick, end_tick]: octave shift, delete,
    collapse octave doubles (upper wins — the 52.3 verdict: safe only
    as a HUMAN decision on a selection), or reassign to another part.
    Rebuilds the affected parts' files; returns new ascii + counts."""
    import json

    if op not in BULK_OPS:
        raise ValueError(f"unknown bulk op: {op}")
    path = _parts_file(out_dir)
    state = json.loads(path.read_text())
    if part_name not in state:
        raise ValueError(f"unknown part: {part_name}")
    if op == "reassign":
        if not target_part or target_part not in state:
            raise ValueError("reassign needs an existing target part")
        if target_part == part_name:
            raise ValueError("reassign target is the source part")

    beats = (scale_beats(shared.beats, opts.tempo_scale)
             if opts.tempo_scale != 1.0 else shared.beats)
    grid = (Grid(beats, subdivision=opts.subdivision)
            if len(beats) > 1 else None)

    def tick_of(start: float) -> int:
        if grid is not None:
            return grid.tick_index(start)
        return round(start / (60.0 / (shared.bpm * opts.tempo_scale)
                              / opts.subdivision))

    part = state[part_name]
    selected = [i for i, n in enumerate(part["notes"])
                if start_tick <= tick_of(n["start"]) <= end_tick]
    if not selected:
        raise ValueError("no notes in the selected range")

    edited = {part_name}
    affected = len(selected)
    if op in ("octave_up", "octave_down"):
        delta = 12 if op == "octave_up" else -12
        for i in selected:
            n = part["notes"][i]
            n["pitch"] = max(0, min(127, n["pitch"] + delta))
    elif op == "delete":
        _drop_notes(part, set(selected))
    elif op == "dedup_octaves":
        # upper-wins inside the selection: drop the LOWER of every
        # time-overlapping ±12 pair (measured +0.015 F1 on Loken,
        # harmful on octave-doubled writing — hence a manual op)
        notes = part["notes"]
        removed: set[int] = set()
        chosen = set(selected)
        for i in selected:
            a = notes[i]
            for j in chosen:
                b = notes[j]
                if (b["pitch"] - a["pitch"] == 12
                        and a["start"] < b["start"] + b["duration"]
                        and b["start"] < a["start"] + a["duration"]):
                    removed.add(i)
                    break
        if not removed:
            raise ValueError("no octave doubles in the selected range")
        affected = len(removed)
        _drop_notes(part, removed)
    elif op == "reassign":
        moved = _drop_notes(part, set(selected))
        _insert_notes(state[target_part], moved)
        edited.add(target_part)

    path.write_text(json.dumps(state))
    ascii_out = _rebuild_outputs(out_dir, state, edited,
                                 shared, opts, grid)
    return {"ascii": ascii_out, "count": affected}


def part_note_meta(out_dir: Path, part_name: str, shared: AnalyzeResult,
                   opts: PipelineOptions) -> list[dict]:
    """Per-note positions (alphaTab quarter-ticks) + confidence for the
    Review mode overlay (task 55)."""
    import json

    state = json.loads(_parts_file(out_dir).read_text())
    if part_name not in state:
        raise ValueError(f"unknown part: {part_name}")
    beats = (scale_beats(shared.beats, opts.tempo_scale)
             if opts.tempo_scale != 1.0 else shared.beats)
    grid = (Grid(beats, subdivision=opts.subdivision)
            if len(beats) > 1 else None)

    def tick_of(start: float) -> int:
        if grid is not None:
            return grid.tick_index(start)
        return round(start / (60.0 / (shared.bpm * opts.tempo_scale)
                              / opts.subdivision))

    return [{"qticks": int(tick_of(n["start"]) * 960 / opts.subdivision),
             "pitch": n["pitch"],
             "conf": n.get("conf", 1.0),
             "dead": n.get("dead", False)}
            for n in state[part_name]["notes"]]


# reference-file instrument words, matching the golden corpus loader's
# convention "<track> (Instrument).mid" (first alpha-only parenthesized
# group names the instrument; multiple parts of one instrument get
# " (2)", " (3)" suffixes like Suno's own exports)
_REFERENCE_WORD = {
    "guitar": "Guitar", "guitar_lead": "Guitar",
    "guitar_rhythm": "Guitar", "bass": "Bass",
    "piano": "Piano", "piano_left": "Piano",
    "vocals": "Vocals", "other": "Synth",
}


def export_reference(out_dir: Path, title: str) -> Path:
    """The human-in-the-loop payoff (task 55): after an edit session
    the corrected project exports as per-instrument MIDI named exactly
    like the golden corpus — the user's correction becomes ground
    truth the eval stand can score future versions against."""
    import json
    import zipfile

    import pretty_midi

    state = json.loads(_parts_file(out_dir).read_text())
    ref_dir = out_dir / "reference"
    ref_dir.mkdir(parents=True, exist_ok=True)
    used: dict[str, int] = {}
    written = []
    for name, p in state.items():
        notes = [n for n in p["notes"] if not n.get("dead")]
        if not notes:
            continue
        word = _REFERENCE_WORD.get(name, name.split("_")[0].capitalize())
        used[word] = used.get(word, 0) + 1
        suffix = f" ({used[word]})" if used[word] > 1 else ""
        profile = profile_for(p["profile"])
        pm = pretty_midi.PrettyMIDI()
        inst = pretty_midi.Instrument(program=profile.midi_program,
                                      is_drum=profile.percussion,
                                      name=name)
        inst.notes = [pretty_midi.Note(
            velocity=n["velocity"], pitch=n["pitch"], start=n["start"],
            end=n["start"] + max(n["duration"], 0.05)) for n in notes]
        pm.instruments.append(inst)
        f = ref_dir / f"{title} ({word}){suffix}.mid"
        pm.write(str(f))
        written.append(f)
    if not written:
        raise ValueError("nothing to export — no notes in any part")
    zip_path = out_dir / "reference.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        for f in written:
            z.write(f, f.name)
    return zip_path


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
        analyzed = run_analyze(audio, out_dir, progress,
                               separator=opts.separator)
    else:
        analyzed = _analyze_mix_only(audio, opts, progress)
    return run_transcribe(out_dir, analyzed, opts, progress)
