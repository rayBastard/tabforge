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
# vocals are deliberately NOT here (user decision, 2026-08-31): the
# vocal stem is still separated (the backing needs it) and synced
# lyrics still run over it (the future karaoke seed), but no vocal
# NOTE track is analyzed or transcribed — vocal transcription fought
# tonality and recitative for months and nobody wanted the result
PITCHED_STEMS = ("guitar", "bass", "piano", "other")


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
    # guitar note source (task 66): "auto" = measured routing
    # (muscriptor; GAPS on acoustic-sounding solo tracks); explicit
    # "bp" | "muscriptor" | "gaps" is the human's override
    guitar_engine: str = "auto"
    # the convenience layer (tasks 58-60), each optional per job
    with_chords: bool = True       # chord line + gp5 labels + sections
    with_lyrics: bool = True       # whisper over the vocal stem
    # lyrics language for the whisper pass (task 60); None = auto
    lyrics_lang: str | None = None
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
            "median_ioi": self.median_ioi,
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
    # set when the project came from a dropped MIDI file: notes are
    # taken from it at face value, no separation/transcription runs
    midi_source: Path | None = None
    # solo mode (task 62): stems all point at the ORIGINAL mix — no
    # separation ran, no leak spectra or backing make sense
    solo: bool = False


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


def _analyze_solo(audio: Path, out_dir: Path,
                  progress: ProgressFn) -> AnalyzeResult:
    """Solo mode (task 62): no separation at all — the mix IS the
    instrument. Tempo and key come from the mix, MT3 (when installed)
    names the dominant instrument for the "Solo track detected" card,
    and every found card points at the ORIGINAL file: the profile's
    own transcriber (or its mix-model route) sees clean audio."""
    from .audio import keydetect, transcribe

    out_dir.mkdir(parents=True, exist_ok=True)
    progress("analyze", "solo mode: no separation, the mix is the "
                        "instrument")
    mix = transcribe.ensure_decodable_wav(audio, out_dir)

    progress("analyze", "tempo and beat grid (mix)")
    mix_data = transcribe.load_audio(mix)
    tempo_extras: dict = {}
    bpm, beats, reliable = transcribe.detect_tempo(mix, audio_data=mix_data,
                                                   extras=tempo_extras)
    warnings = [] if reliable else ["tempo: estimated poorly"]
    try:
        key = keydetect.detect_key(mix, audio_data=mix_data)
        progress("analyze", f"key: {key.name}")
    except Exception:  # noqa: BLE001
        key = None
        warnings.append("key: detection failed")

    # both whole-mix models cache their MIDI here for routing/verdicts;
    # the solo-detect merges BOTH opinions per card — MT3 hears keys
    # and pads, MuScriptor hears guitar and bass (a clean solo guitar
    # landed on the "other" card when MT3 judged alone)
    import soundfile as sf
    minutes = max(sf.info(str(mix)).frames
                  / sf.info(str(mix)).samplerate / 60, 0.1)
    densities: dict[str, float] = {}
    try:
        from .audio import arbiter
        if arbiter.find_mt3() is not None:
            midi = arbiter.run_mt3(mix, out_dir, progress)
            if midi is not None:
                densities = arbiter.mt3_densities(midi, minutes)
    except Exception:  # noqa: BLE001
        pass
    try:
        from .audio.muscriptor import find_muscriptor, run_muscriptor
        if find_muscriptor() is not None:
            m = run_muscriptor(mix, out_dir, progress)
            if m is not None:
                from .audio.arbiter import mt3_densities
                for card, d in mt3_densities(m, minutes).items():
                    densities[card] = max(densities.get(card, 0.0), d)
    except Exception:  # noqa: BLE001
        pass

    cards = (*PITCHED_STEMS, "drums")
    analysis: dict[str, StemAnalysis] = {}
    densities.pop("vocals", None)     # no vocal note track (see PITCHED_STEMS)
    if densities:
        dominant = max(densities, key=densities.get)
        if dominant == "other":
            # the catch-basin wins only when no NAMED instrument comes
            # close — a solo guitar half-heard as "other" is a guitar
            named = {c: d for c, d in densities.items() if c != "other"}
            if named:
                best = max(named, key=named.get)
                if named[best] >= 0.4 * densities["other"]:
                    dominant = best
        for card in cards:
            dens = densities.get(card, 0.0)
            heard = dens >= (60.0 if card == "drums" else 5.0)
            if card == dominant:
                # the ONE preselected card — this is a solo track
                analysis[card] = StemAnalysis(card, "found", 1.0,
                                              verdict="found")
            elif heard:
                # MT3 heard echoes of other timbres in the same
                # instrument: offer the card unchecked, human decides
                analysis[card] = StemAnalysis(card, "quiet", 0.5)
            else:
                analysis[card] = StemAnalysis(card, "absent", 0.0,
                                              verdict="absent")
        progress("analyze",
                 f"solo track detected: {dominant}")
        warnings.append(f"solo: detected {dominant}")
        count, lo, hi, ioi = _quick_note_stats(mix, dominant, out_dir)
        a = analysis[dominant]
        a.note_count = count
        a.min_pitch, a.max_pitch = lo, hi
        a.median_ioi = ioi
        a.suggested_tuning = suggest_tuning(dominant, lo)
        # tempo-octave correction from the solo instrument's own
        # rhythm (the "Просто так вышло" case: detector said 81, the
        # sixteenth-dense strumming and 36% of raw votes said 162)
        bpm, beats = _octave_correct(bpm, beats, ioi,
                                     tempo_extras.get("local_votes"),
                                     progress)
        beats = _elect_bar_phase(mix, mix_data, beats, progress)
        from .audio.tagging import tag_stem
        a.sounds_like = tag_stem(mix)
    else:
        # no arbiter: the user KNOWS it's solo — offer every card,
        # nothing preselected beyond their judgement
        for card in cards:
            analysis[card] = StemAnalysis(card, "quiet", 0.5)
        progress("analyze", "solo mode without MT3: pick the "
                            "instrument yourself")

    try:
        from .audio.sections import compute_features
        compute_features(mix, beats, out_dir / "sections_features.npz")
    except Exception:  # noqa: BLE001
        pass

    stems = {card: mix for card, a in analysis.items()
             if a.status != "absent"}
    return AnalyzeResult(stems=stems, analysis=analysis, bpm=bpm,
                         beats=beats, tempo_reliable=reliable, key=key,
                         warnings=warnings, solo=True)


def _elect_bar_phase(mix: Path, mix_data, beats: list[float],
                     progress: ProgressFn = _noop) -> list[float]:
    """Bar 1 must start at a REAL downbeat (task 71). Of the four
    possible bar phases of the beat grid, elect the one whose bar
    lines carry the largest beat-to-beat CHROMA change — chords
    change on bar lines (harmonic rhythm). Measured on the meter
    stand: hits the beat-grid's own F1 ceiling wherever the grid is
    good (Fulgrim .62, Hero .86, keys .93), and unlike the old
    "first tracked beat" phase it is deterministic — that one flipped
    0.86 -> 0.01 between runs on separation jitter alone.

    The elected phase is applied by PREPENDING extrapolated beats so
    the grid still covers the audio from the start (the intro-crush
    guard, v0.7.9) and beats[0] is a downbeat."""
    if len(beats) < 8:
        return beats
    try:
        import librosa
        import numpy as np

        if mix_data is None:
            from .audio import transcribe as _T
            mix_data = _T.load_audio(mix)
        y, sr = mix_data
        C = librosa.feature.chroma_cqt(y=y, sr=sr)
        frames = np.clip(librosa.time_to_frames(beats, sr=sr),
                         0, C.shape[1] - 1)
        sync = librosa.util.sync(C, frames, aggregate=np.median)
        chroma = sync.T[:len(beats)]
        chroma = chroma / (np.linalg.norm(chroma, axis=1,
                                          keepdims=True) + 1e-9)
        change = np.zeros(len(beats))
        for i in range(1, len(chroma)):
            change[i] = 1.0 - float(np.dot(chroma[i - 1], chroma[i]))
        # sync segments sit one step behind the beat list — the change
        # "at" segment i marks the bar line at beat i-1 (the stand
        # found the off-by-one: unshifted scoring inverted every phase)
        phase = max(range(4),
                    key=lambda ph: float(np.mean(change[ph + 1::4])
                                         if len(change[ph + 1::4])
                                         else 0.0))
    except Exception:  # noqa: BLE001 — the phase is a refinement
        return beats
    if phase == 0:
        return beats
    k = (4 - phase) % 4
    step = beats[1] - beats[0]
    prefix = [beats[phase] - (i + 1) * step
              for i in range(phase + k - 1, -1, -1)]
    # prefix rebuilds the beats BEFORE the elected downbeat plus one
    # extrapolated bar, so beats[0::4] are downbeats and the grid
    # still reaches (before) the start of the audio
    out = prefix + beats[phase:]
    progress("analyze",
             f"bars: phase {phase} elected by harmonic rhythm")
    return out


def _octave_correct(bpm: float, beats: list[float],
                    median_ioi: float | None,
                    local_votes: list[float] | None,
                    progress: ProgressFn = _noop
                    ) -> tuple[float, list[float]]:
    """The tempo-octave rule (2026-08-30, benched 9/9 on every track
    with known tempo — docs/eval.md "THE TEMPO OCTAVE"): the beat
    tracker's family choice is right, its OCTAVE sometimes is not,
    and note evidence settles it.

    - material at the beat rate (median IOI >= 0.9 beat: nothing
      moves between beats) -> HALF time. Generalizes the task-56
      keys rule (Fulgrim 161.5 -> 80.8, solo Keyboard likewise).
    - sixteenth-dense material (IOI <= 0.35 beat) whose doubled tempo
      the audio itself VOTES for (>= 10% of raw periodicity votes
      within 5% of 2x bpm) -> DOUBLE time. The vote condition is the
      real guard: 16th songs at a true 96 carry ~0-2% votes at 2x
      (Loken/Bass/Guitar) while the halved-tempo victim carried 36%.
    """
    if not median_ioi or len(beats) < 4:
        return bpm, beats
    beat = 60.0 / bpm
    ratio = median_ioi / beat
    if ratio >= 0.9 and bpm / 2 >= 55:
        progress("analyze",
                 f"tempo: material moves at the beat rate — half time "
                 f"({bpm / 2:.0f} BPM)")
        return bpm / 2, beats[::2]
    if ratio <= 0.35 and bpm * 2 <= 185 and local_votes:
        target = 2 * bpm
        share = (sum(1 for v in local_votes
                     if abs(v - target) <= 0.05 * target)
                 / max(len(local_votes), 1))
        if share >= 0.10:
            doubled = [beats[0]]
            for a, b in zip(beats, beats[1:]):
                doubled += [(a + b) / 2, b]
            progress("analyze",
                     f"tempo: sixteenth-dense material and the audio "
                     f"votes for {target:.0f} BPM — double time")
            return bpm * 2, doubled
    return bpm, beats


def run_analyze(audio: Path, out_dir: Path,
                progress: ProgressFn = _noop,
                cancel_token: object | None = None,
                separator: str = "demucs",
                use_mt3: bool = True,
                solo: bool = False) -> AnalyzeResult:
    """Separate + quick per-stem facts + shared tempo/key. No demucs work
    is ever repeated after this: the stems stay in out_dir/stems.

    cancel_token lets a caller abort the demucs subprocess mid-run via
    transcribe.abort_separation(token); cooperative cancellation between
    stages happens by raising from the progress callback."""
    import numpy as np

    from .audio import keydetect, transcribe

    if solo:
        return _analyze_solo(audio, out_dir, progress)

    out_dir.mkdir(parents=True, exist_ok=True)
    demucs_input = transcribe.ensure_decodable_wav(audio, out_dir)

    # the whole-mix models need only the MIX — warm their caches in a
    # side thread WHILE demucs separates, instead of after it: the
    # analyze wall time becomes max(demucs+stats, MT3) instead of the
    # sum (MT3 alone is ~1x track length)
    import threading

    def _warm_mix_models() -> None:
        try:
            from .audio import arbiter as _arb
            if use_mt3 and _arb.find_mt3() is not None:
                _arb.run_mt3(demucs_input, out_dir, progress)
        except Exception:  # noqa: BLE001
            pass
        try:
            from .audio.muscriptor import find_muscriptor, run_muscriptor
            if find_muscriptor() is not None:
                run_muscriptor(demucs_input, out_dir, progress)
        except Exception:  # noqa: BLE001
            pass

    warm_thread = threading.Thread(target=_warm_mix_models, daemon=True)
    warm_thread.start()

    progress("separate", "Separating into stems (first run downloads the model)")
    all_stems = transcribe.separate(demucs_input, out_dir / "stems",
                                    backend=separator,
                                    cancel_token=cancel_token)

    warnings: list[str] = []

    progress("analyze", "tempo and beat grid")
    tempo_source, source_name = choose_tempo_source(
        all_stems, audio, transcribe.stem_is_audible)
    mix_data = transcribe.load_audio(audio) if tempo_source == audio else None
    tempo_extras: dict = {}
    bpm, beats, tempo_reliable = transcribe.detect_tempo(
        tempo_source, audio_data=mix_data, extras=tempo_extras)
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
            warm_thread.join()          # mt3.mid is (about to be) cached
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

    # MuScriptor cache: normally warmed by the side thread already;
    # this is the safety net when the thread died early
    try:
        warm_thread.join()
        from .audio.muscriptor import find_muscriptor, run_muscriptor
        if find_muscriptor() is not None:
            run_muscriptor(demucs_input, out_dir, progress)
    except Exception:  # noqa: BLE001 — optional backend, never fatal
        progress("analyze", "MuScriptor failed — stem transcription "
                            "will keep its instruments")

    # Tempo-octave correction (generalizes the task-56 keys rule; the
    # UI selector still lets the user override either way). The note
    # evidence: the fastest FOUND card's median inter-onset interval.
    found_iois = [a.median_ioi for a in analysis.values()
                  if a.status == "found" and a.median_ioi]
    if found_iois:
        # drum-tracked tempi get NO exemption: the tracker octave-errs
        # on real kits too (the user's mix: drums said 80.7 while the
        # drum stem itself votes 14% for 161.5 and the guitar moves at
        # 106 ms). The three guards — IOI ratio, the 185 ceiling and
        # the raw-vote share — are what actually protect true tempos
        # (benched: Techno/Hero blocked by ratio, Loken by the ceiling
        # with 0.6% votes to spare).
        bpm, beats = _octave_correct(bpm, beats, min(found_iois),
                                     tempo_extras.get("local_votes"),
                                     progress)
    beats = _elect_bar_phase(audio, mix_data, beats, progress)

    # structure features (task 59): beat-synced chroma of the MIX —
    # cached now, while the mix is at hand; boundaries are detected at
    # transcribe time where the chord line exists as a second voice
    try:
        from .audio.sections import compute_features
        compute_features(demucs_input, beats,
                         out_dir / "sections_features.npz")
    except Exception:  # noqa: BLE001 — decoration, never fatal
        pass

    return AnalyzeResult(stems=all_stems, analysis=analysis,
                         bpm=bpm, beats=beats,
                         tempo_reliable=tempo_reliable, key=key,
                         warnings=warnings)


def run_analyze_midi(midi: Path, out_dir: Path,
                     progress: ProgressFn = _noop) -> AnalyzeResult:
    """The MIDI drop path: instrument cards, tempo grid and key from
    the file itself — ready for the same picker and run_transcribe."""
    from collections import Counter

    from .audio.keydetect import detect_key_from_chroma
    from .audio.midi_in import load_midi_classes, midi_project_facts

    out_dir.mkdir(parents=True, exist_ok=True)
    progress("analyze", "reading the MIDI file")
    classes = load_midi_classes(midi)
    bpm, beats, _dur = midi_project_facts(midi)
    progress("analyze", f"tempo: {bpm:.1f} BPM (from the MIDI tempo map)")

    key = None
    pitched = [n for card, notes in classes.items() if card != "drums"
               for n in notes]
    if pitched:
        chroma = [0.0] * 12
        for n in pitched:
            chroma[n.pitch % 12] += n.duration
        try:
            key = detect_key_from_chroma(chroma)
            progress("analyze", f"key: {key.name}")
        except Exception:  # noqa: BLE001
            key = None

    analysis: dict[str, StemAnalysis] = {}
    for card in (*PITCHED_STEMS, "drums"):
        notes = classes.get(card, [])
        if not notes:
            analysis[card] = StemAnalysis(card, "absent", 0.0)
            continue
        hist = Counter(n.pitch for n in notes)
        pitches = sorted(hist)
        analysis[card] = StemAnalysis(
            card, "found", 1.0, note_count=len(notes),
            min_pitch=None if card == "drums" else pitches[0],
            max_pitch=None if card == "drums" else pitches[-1],
            suggested_tuning=suggest_tuning(card, pitches[0]))
        progress("analyze", f"{card}: {len(notes)} notes in the file")

    return AnalyzeResult(stems={}, analysis=analysis, bpm=bpm,
                         beats=beats, tempo_reliable=len(beats) > 3,
                         key=key, midi_source=midi)


def run_transcribe(out_dir: Path, analyzed: AnalyzeResult,
                   opts: PipelineOptions,
                   progress: ProgressFn = _noop) -> list[StemResult]:
    """Transcribe the SELECTED stems using the cached separation and the
    shared grid/key from run_analyze."""
    from .audio import transcribe
    from .export import writers

    # a dropped MIDI file: the classes ARE the notes — no wavs anywhere
    midi_classes = None
    if analyzed.midi_source is not None:
        from .audio.midi_in import load_midi_classes
        midi_classes = load_midi_classes(analyzed.midi_source)

    # demucs emits drums first; a score reads melodic-top, drums-bottom
    part_order = {name: i for i, name in enumerate((*PITCHED_STEMS, "drums"))}
    source_map = (analyzed.stems if midi_classes is None
                  else {k: analyzed.midi_source for k in midi_classes})
    stems = dict(sorted(
        ((k, v) for k, v in source_map.items()
         if k in opts.stems and k != "vocals"),
        key=lambda kv: part_order.get(kv[0], len(part_order))))

    # Everything the user did NOT pick becomes a play-along backing track.
    if midi_classes is None and not analyzed.solo:
        backing_dir = out_dir / "backing"
        backing_dir.mkdir(parents=True, exist_ok=True)
        if transcribe.mix_backing(analyzed.stems, opts.stems,
                                  backing_dir / "backing.wav"):
            progress("transcribe",
                     "backing track mixed from unselected stems")

    bpm, beats, key = analyzed.bpm, analyzed.beats, analyzed.key
    if opts.tempo_scale != 1.0:
        beats = scale_beats(beats, opts.tempo_scale)
        bpm = bpm * opts.tempo_scale
    warnings = list(analyzed.warnings)
    grid = Grid(beats, subdivision=opts.subdivision) if len(beats) > 1 else None

    # shared spectrogram cache for the leak filter (one STFT per stem)
    from .audio.validate import _StemSpectra, filter_leaked_notes
    spectra = (_StemSpectra(analyzed.stems)
               if opts.leak_margin > 0 and analyzed.stems
               and not analyzed.solo else None)

    results: list[StemResult] = []
    song_parts: list = []          # writers.SongPart, one per produced part
    for name, wav in stems.items():
        if name == "drums":
            result = _transcribe_drums_part(
                out_dir, wav, analyzed, opts, grid, warnings, song_parts,
                progress,
                hits=(midi_classes or {}).get("drums"))
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
        # the guitar engine (task 66): auto = the measured routing —
        # MuScriptor everywhere EXCEPT acoustic-sounding solo tracks,
        # where GAPS wins its home domain (GuitarSet 0.858 vs 0.745);
        # explicit bp/muscriptor/gaps overrides, the human's call
        if name == "guitar" and opts.treat.get(name, name) == "guitar":
            engine = opts.guitar_engine
            if engine == "auto" and analyzed.solo:
                from .audio.gaps import available as _gaps_ok
                from .audio.gaps import sounds_acoustic
                if _gaps_ok() and sounds_acoustic(wav):
                    engine = "gaps"
                    progress("transcribe",
                             "guitar: sounds acoustic — GAPS engine")
            if engine == "bp":
                source = "stem"
            elif engine == "muscriptor":
                source = "muscriptor"
            elif engine == "gaps":
                from .audio.gaps import transcribe_gaps
                g = transcribe_gaps(wav, progress)
                if g:
                    notes = g
                    from_mt3 = True
        if from_mt3:
            pass
        elif midi_classes is not None:
            notes = list(midi_classes.get(name, []))
            progress("transcribe",
                     f"{name}: {len(notes)} notes from the MIDI file")
            from_mt3 = True          # face-value notes: no leak filter,
                                     # no stem-spectrum confidence
        elif (source in ("mt3", "muscriptor")
                and opts.treat.get(name, name) == name):
            from .audio.arbiter import mt3_card_notes
            # keys prefer muscriptor-MEDIUM over MT3 when it produced
            # the cache (golden piano 0.65 vs 0.57 — the >=0.05 rule).
            # MT3-as-note-source is KEPT even though medium beats it:
            # see eval.md "NOTE-SOURCE ROUTING (task 57)" — it wins for
            # every install without the gated MuScriptor weights.
            sources = [source]
            if source == "mt3":
                marker = out_dir / "muscriptor.variant"
                if (marker.exists()
                        and marker.read_text().strip() == "medium"):
                    sources.insert(0, "muscriptor")
            mix_notes = None
            for cand in sources:
                cache = out_dir / f"{cand}.mid"
                mix_notes = mt3_card_notes(cache, name)
                if mix_notes:
                    source = cand
                    break
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
            mono_kw = MONO_PRESETS.get(name, {})
            # the chooser needs a RATIO, not the notes — decide on a
            # 60 s slice instead of paying full-stem pyin (task 68:
            # 35 s of pyin on a vocal stem that then went to Basic
            # Pitch anyway), then run only the WINNER on the full
            # stem. VOCALS ONLY: on bass the probe flips the decision
            # (Techno/Hero middles are pyin-friendly, the full stems
            # are not) and the flip was measured to COST F1 (Hero
            # bass: bp 0.23 vs mono 0.18) — bass keeps the full-stem
            # chooser.
            probe = (_chooser_sample(wav, out_dir)
                     if name == "vocals" else wav)
            mono_probe = transcribe_mono(probe, **mono_kw)
            bp_probe = transcribe.cleanup(
                transcribe.transcribe_stem(probe, **preset),
                max_polyphony=1 if name == "bass" else 6)
            if probe is not wav:
                probe.unlink(missing_ok=True)
            if len(mono_probe) >= 0.4 * max(len(bp_probe), 1):
                notes = (mono_probe if probe is wav
                         else transcribe_mono(wav, **mono_kw))
                progress("transcribe", f"{name}: monophonic f0 path "
                                       f"({len(notes)} notes)")
            else:
                progress("transcribe",
                         f"{name}: stem too dense for the mono tracker "
                         f"({len(mono_probe)} vs {len(bp_probe)} on the "
                         f"probe), Basic Pitch keeps it")
                notes = (bp_probe if probe is wav
                         else transcribe.cleanup(
                             transcribe.transcribe_stem(wav, **preset),
                             max_polyphony=1 if name == "bass" else 6))
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
        if role_of(name) == "piano":
            # a grand staff is two tracks: right hand (treble) and left
            # hand (bass); the frontend renders them as one Keys view.
            # ANY keys-role part qualifies — "other" (strings, synths)
            # reaches down below the treble staff just as often
            hands = split_hands(notes)
            if hands is not None:
                right, left = hands
                parts = [(name, right), (f"{name}_left", left)]
                progress("fingering",
                         f"{name}: grand staff — {len(right)} right-hand "
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
            if profile.name == "bass":
                # the card only SUGGESTED a 5-string before; the part
                # itself must actually switch when the material dives
                # below E1, or the low notes have no string to live on
                pitched = [n.pitch for n in part_notes if not n.dead]
                if pitched:
                    tuning_key = suggest_tuning("bass", min(pitched)) \
                        or tuning_key
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

    # synced lyrics (task 60): whisper over the vocal stem — optional,
    # attaches the .lrc to the vocals card and feeds the gp5 channel
    vocal_wav = analyzed.stems.get("vocals")
    if (vocal_wav is not None and opts.with_lyrics
            and analyzed.midi_source is None):
        try:
            _run_lyrics(out_dir, vocal_wav, grid, opts, progress)
            lrc = out_dir / "vocals" / "lyrics.lrc"
            if lrc.exists():
                for r in results:
                    if r.stem == "vocals":
                        r.files["lrc"] = lrc
        except Exception as e:  # noqa: BLE001 — decoration, never fatal
            progress("transcribe", f"lyrics failed ({e})")

    # the whole project as ONE multi-track score: the unified player
    # plays it together, with per-track mute/solo
    if song_parts:
        progress("export", "assembling the multi-track project score")
        song_dir = out_dir / "song"
        song_dir.mkdir(parents=True, exist_ok=True)
        chord_labels = None
        try:
            import json as _json
            if not opts.with_chords:
                raise StopIteration      # skipped by choice, not error
            state = _json.loads(_parts_file(out_dir).read_text())
            chord_data = _compute_chords(out_dir, state, beats, key,
                                         grid, opts, song_parts)
            chord_labels = [(c["qticks"], c["name"], c["frets"])
                            for c in chord_data]
            if chord_data:
                progress("export",
                         f"chord line: {len(chord_data)} chords")
        except StopIteration:
            pass
        except Exception as e:  # noqa: BLE001 — decoration, never fatal
            progress("export", f"chord line failed to build ({e})")
        section_marks = None
        try:
            if not opts.with_chords:
                raise StopIteration
            chord_src = chord_data if chord_labels else None
            section_marks = _sections_for_export(
                out_dir, beats, opts, grid, chord_src, redetect=True)
            if section_marks:
                progress("export",
                         f"structure: {len(section_marks)} sections "
                         + "·".join(l for _, l in section_marks[:6]))
        except StopIteration:
            pass
        except Exception as e:  # noqa: BLE001
            progress("export", f"section detection failed ({e})")
        try:
            writers.export_song_gp5(
                song_parts, song_dir / "song.gp5",
                bpm=bpm, beats_per_measure=opts.beats_per_measure,
                subdivision=opts.subdivision, title="TabForge project",
                key=key, grid=grid, chords=chord_labels,
                sections=section_marks,
                lyrics=_lyrics_for_export(out_dir, opts))
        except Exception as e:
            # repr, not str: a bare StopIteration once hid here as "()"
            progress("export", f"project score failed to build ({e!r})")
    return results


def _transcribe_drums_part(out_dir: Path, wav: Path,
                           analyzed: AnalyzeResult, opts: PipelineOptions,
                           grid, warnings: list[str], song_parts: list,
                           progress: ProgressFn,
                           hits: list | None = None) -> StemResult | None:
    """The percussion branch of run_transcribe: onsets instead of
    Basic Pitch, kit voices instead of a fretboard — so no fingering
    search, no pins, and no parts.json entry (nothing to re-pin).
    `hits` (GM-pitched NoteEvents from a dropped MIDI) skips the
    audio classifier entirely."""
    from .audio import drums as drum_mod
    from .export import writers

    if hits is None:
        progress("transcribe", "drums: detecting hits")
        hits = drum_mod.transcribe_drums(wav)
    else:
        progress("transcribe", f"drums: {len(hits)} hits from the MIDI file")
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


def _chooser_sample(wav: Path, out_dir: Path,
                    sample_s: float = 60.0) -> Path:
    """The middle sample_s of a stem for the mono-vs-BP chooser.
    Returns the stem itself when it is short enough already."""
    import soundfile as sf

    info = sf.info(str(wav))
    total_s = info.frames / info.samplerate
    if total_s <= sample_s * 1.5:
        return wav
    start = int((total_s - sample_s) / 2 * info.samplerate)
    data, sr = sf.read(str(wav), start=start,
                       frames=int(sample_s * info.samplerate),
                       always_2d=True)
    probe = out_dir / f"_chooser_{wav.stem}.wav"
    sf.write(str(probe), data, sr)
    return probe


def _fine_indexer(beats: list[float], bpm: float):
    """Time -> fine tick (24 units per beat), the editor's addressing
    grid — the same base the adaptive gp5 writer slots notes on."""
    if len(beats) > 1:
        fine_grid = Grid(beats, subdivision=24)
        return fine_grid.tick_index
    fine_len = 60.0 / max(bpm, 1e-6) / 24
    return lambda t: int(round(t / fine_len))


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
    fine = _fine_indexer(beats, shared.bpm * opts.tempo_scale)

    def revive(part):
        return [NoteEvent(n["pitch"], n["start"], n["duration"],
                          n["velocity"], list(n["bends"]),
                          n.get("dead", False))
                for n in part["notes"]]

    # locate the clicked note: same FINE position (24 units per beat —
    # matches the adaptive score grid, so a 32nd run cannot alias onto
    # a neighbor; the collision shift allows a 32nd of slack) and pitch
    part = state[part_name]
    notes = revive(part)
    target = None
    for i, n in enumerate(notes):
        if n.pitch != pitch:
            continue
        if abs(fine(n.start) - tick) <= 3:
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


def apply_repin_group(out_dir: Path, part_name: str,
                      shared: AnalyzeResult, opts: PipelineOptions,
                      pitch: int | None = None,
                      string: int | None = None,
                      from_tick: int | None = None,
                      to_tick: int | None = None,
                      restore: dict | None = None) -> dict:
    """Group edit (2026-08-30, the user's ask): pin EVERY note of one
    pitch to a string in one stroke — across the whole part, or only
    inside [from_tick, to_tick] (fine units, 24/beat) when a range is
    given. string=None removes those pins instead.

    restore={note_index: pin_or_None} is the undo path: it puts the
    previous pins back verbatim and ignores the other arguments.
    Returns {'count', 'prev_pins', 'ascii'}."""
    import json

    path = _parts_file(out_dir)
    state = json.loads(path.read_text())
    if part_name not in state:
        raise ValueError(f"unknown part: {part_name}")
    part = state[part_name]
    pins = {int(k): v for k, v in part["pins"].items()}
    prev_pins: dict[int, int | None] = {}

    if restore is not None:
        for k, v in restore.items():
            i = int(k)
            prev_pins[i] = pins.get(i)
            if v is None:
                pins.pop(i, None)
            else:
                pins[i] = int(v)
    else:
        if pitch is None:
            raise ValueError("group repin needs a pitch")
        beats = (scale_beats(shared.beats, opts.tempo_scale)
                 if opts.tempo_scale != 1.0 else shared.beats)
        fine = _fine_indexer(beats, shared.bpm * opts.tempo_scale)
        for i, n in enumerate(part["notes"]):
            if n["pitch"] != pitch or n.get("dead"):
                continue
            t = fine(n["start"])
            if from_tick is not None and t < from_tick - 3:
                continue
            if to_tick is not None and t > to_tick + 3:
                continue
            prev_pins[i] = pins.get(i)
            if string is None:
                pins.pop(i, None)
            else:
                pins[i] = int(string)
        if not prev_pins:
            raise ValueError("no notes of that pitch in the range")

    part["pins"] = {str(k): v for k, v in pins.items()}
    path.write_text(json.dumps(state))

    beats = (scale_beats(shared.beats, opts.tempo_scale)
             if opts.tempo_scale != 1.0 else shared.beats)
    grid = (Grid(beats, subdivision=opts.subdivision)
            if len(beats) > 1 else None)
    edited_ascii = _rebuild_outputs(out_dir, state, {part_name},
                                    shared, opts, grid)
    return {"count": len(prev_pins),
            "prev_pins": {str(k): v for k, v in prev_pins.items()},
            "ascii": edited_ascii.get(part_name, "")}


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

    chord_labels = None
    section_marks = None
    try:
        beats = (scale_beats(shared.beats, opts.tempo_scale)
                 if opts.tempo_scale != 1.0 else shared.beats)
        chord_data = _compute_chords(out_dir, state, beats, shared.key,
                                     grid, opts, song_parts)
        chord_labels = [(c["qticks"], c["name"], c["frets"])
                        for c in chord_data]
        # reuse sections.json: the user's renames must survive edits
        section_marks = _sections_for_export(
            out_dir, beats, opts, grid, chord_data, redetect=False)
    except Exception:  # noqa: BLE001 — decoration, never fatal
        pass
    writers.export_song_gp5(song_parts, out_dir / "song" / "song.gp5",
                            bpm=shared.bpm * opts.tempo_scale,
                            beats_per_measure=opts.beats_per_measure,
                            subdivision=opts.subdivision,
                            title="TabForge project", key=shared.key,
                            grid=grid, chords=chord_labels,
                            sections=section_marks,
                            lyrics=_lyrics_for_export(out_dir, opts))
    return ascii_out


def _compute_chords(out_dir: Path, state: dict, beats: list[float],
                    key, grid, opts: PipelineOptions,
                    song_parts=None) -> list[dict]:
    """The chord line (task 58): pooled harmony of every pitched part,
    segmented over the beat grid; diagrams come from OUR tab shapes
    where a guitar actually plays the span, else from a standard
    voicing laid out by the same fretboard engine. Persisted as
    chords.json for the UI and returned for the gp5 labels."""
    import json

    from .core.chords import track_chords
    from .core.fretboard import assign_tab

    notes = []
    for p in state.values():
        notes.extend(_revive_notes(p))
    spans = track_chords(notes, beats)
    flats = bool(key and key.accidentals < 0)

    guitar_shapes = []
    n_strings = 6
    if song_parts:
        for sp in song_parts:
            if sp.profile.tablature and sp.name.startswith("guitar"):
                guitar_shapes.extend(sp.shapes)
                n_strings = len(sp.cfg.tuning)
        guitar_shapes.sort(key=lambda s: s.start)

    def tick_of(start: float) -> int:
        if grid is not None:
            return grid.tick_index(start)
        return round(start * opts.subdivision / (60.0 / max(opts.tempo_scale, 1e-6)))

    def frets_for(span) -> tuple[list[int] | None, list[int]]:
        # the shape the tab actually plays in this span (>=2 strings)
        for s in guitar_shapes:
            if span.start - 0.05 <= s.start < span.end:
                if len(s.placements) >= 2:
                    frets = [-1] * n_strings
                    pitches = []
                    for pl in sorted(s.placements, key=lambda x: x.string):
                        frets[pl.string] = pl.fret   # low string first
                        pitches.append(pl.note.pitch)
                    return frets, pitches
        # keys-only harmony: a standard voicing via the same engine
        from .core import TabConfig
        root = 40 + ((span.guess.root - 4) % 12)
        chord_notes = [NoteEvent(root, 0.0, 1.0),
                       NoteEvent(root + 7, 0.0, 1.0),
                       NoteEvent(root + 12, 0.0, 1.0)]
        third = {"m": 3, "m7": 3, "m add9": 3, "dim": 3}.get(
            span.guess.suffix, None if span.guess.suffix in ("5", "sus2", "sus4") else 4)
        if third is not None:
            chord_notes.append(NoteEvent(root + 12 + third, 0.0, 1.0))
        cfg = TabConfig()
        shapes = assign_tab(chord_notes, cfg)
        if not shapes or not shapes[0].placements:
            return None, [n.pitch for n in chord_notes]
        frets = [-1] * 6
        pitches = []
        for pl in sorted(shapes[0].placements, key=lambda x: x.string):
            frets[pl.string] = pl.fret               # low string first
            pitches.append(pl.note.pitch)
        return frets, pitches

    out = []
    for span in spans:
        frets, pitches = frets_for(span)
        out.append({
            "start": span.start, "end": span.end,
            "qticks": int(tick_of(span.start) * 960 / opts.subdivision),
            "name": span.guess.name(flats),
            "frets": frets,
            "pitches": pitches,
        })
    (out_dir / "chords.json").write_text(json.dumps(out))
    return out


def _lyrics_for_export(out_dir: Path,
                       opts: PipelineOptions) -> tuple[str, int, str] | None:
    """(vocals_part_name, starting_measure, text) for the gp5 lyrics
    channel, honoring hidden segments. None when no lyrics exist."""
    import json

    path = out_dir / "lyrics.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    words, first_q = [], None
    for seg in data.get("segments", []):
        if seg.get("hidden"):
            continue
        for w in seg["words"]:
            words.append(w["word"])
            if first_q is None:
                first_q = w.get("qticks", 0)
    if not words:
        return None
    measure = int((first_q or 0) // (960 * opts.beats_per_measure)) + 1
    return "vocals", measure, " ".join(words)


def _run_lyrics(out_dir: Path, vocals_wav: Path, grid,
                opts: PipelineOptions, progress: ProgressFn) -> None:
    """Whisper over the vocal stem -> lyrics.json (+ .lrc). Optional:
    silently absent without the tabforge[lyrics] extra."""
    import json

    from .audio import lyrics as L

    if not L.available():
        return
    data = L.transcribe_lyrics(vocals_wav, opts.lyrics_lang, progress)
    if not data or not data["segments"]:
        return
    for seg in data["segments"]:
        for w in seg["words"]:
            tick = (grid.tick_index(w["start"]) if grid is not None
                    else 0)
            w["qticks"] = int(tick * 960 / opts.subdivision)
    (out_dir / "lyrics.json").write_text(json.dumps(data))
    vocals_dir = out_dir / "vocals"
    vocals_dir.mkdir(parents=True, exist_ok=True)
    (vocals_dir / "lyrics.lrc").write_text(L.to_lrc(data))
    n_words = sum(len(s["words"]) for s in data["segments"])
    junk = sum(1 for s in data["segments"] if s["junk"])
    progress("transcribe",
             f"lyrics: {n_words} words ({data['language']}), "
             f"{junk} segment(s) look like non-words")


def _sections_for_export(out_dir: Path, beats: list[float],
                         opts: PipelineOptions, grid,
                         chord_data: list[dict] | None,
                         redetect: bool) -> list[tuple[int, str]]:
    """Section spans for the gp5 markers and the UI. redetect=True
    (a fresh transcription) runs detection and overwrites
    sections.json; False (rebuilds after edits/renames) reuses the
    file so the user's names survive."""
    import json

    path = out_dir / "sections.json"
    if redetect or not path.exists():
        from .audio.sections import detect_sections
        secs = detect_sections(out_dir / "sections_features.npz",
                               beats, opts.beats_per_measure, chord_data)
        if not secs:
            return []

        def tick_of(start: float) -> int:
            if grid is not None:
                return grid.tick_index(start)
            return 0
        for s in secs:
            s["qticks"] = int(tick_of(s["start"]) * 960 / opts.subdivision)
        path.write_text(json.dumps(secs))
    else:
        secs = json.loads(path.read_text())
    return [(s["qticks"], s["label"]) for s in secs]


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
