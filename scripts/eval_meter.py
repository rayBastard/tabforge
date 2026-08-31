"""
The rhythm/meter stand (task 70).

Truth: Suno MIDIs carry NO explicit time-signature meta — pretty_midi
derives bars from the default 4/4 over the real tempo grid. On this
corpus the meter truth is therefore trivially 4/4 (a 3/4 track is
wanted for the meter-accuracy metric); the PHASE truth — where bars
start — is solid and is the metric the whole block 70-74 targets.

Metrics (per track and mean):
  beat F1        est beat grid vs truth quarter grid, 70 ms window
  downbeat F1    est downbeats (currently beats[0::4] — the 4/4-from-
                 first-beat assumption) vs truth bar starts, 70 ms
  meter acc      est meter (currently always 4) vs truth numerator

Alignment protocol: per-track MIDI->audio offset by CROSS-CORRELATING
the truth note-onset train with the audio's onset envelope (sub-beat
sharp and unique; a beat-grid sweep degenerates modulo the beat when
the grid itself is noisy — measured: Fulgrim's sweep flipped between
-0.31 and +0.51, exactly one beat apart). Frozen in OFFSETS after
--calibrate.

    .venv/bin/python scripts/eval_meter.py [--calibrate]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import eval_golden  # noqa: F401,E402 — mido patch for Suno keys

TM = ROOT / "Tracks and midi"
TRACKS = [
    ("fulgrim", TM / "FulgrimUpd.wav", TM / "Fulgrim (Piano) 2.mid"),
    ("hero", TM / "Hero of Mankind.mp3",
     TM / "Hero of Mankind (Guitar).mid"),
    ("loken", TM / "Loken (The End And The Death).mp3",
     TM / "Loken (The End And The Death) (Guitar) (1).mid"),
    ("solo-bass", TM / "Only instruments/Bass.mp3",
     TM / "Only instruments/Bass (Bass).mid"),
    ("solo-guitar", TM / "Only instruments/Guitar.mp3",
     TM / "Only instruments/Guitar (Electric Guitar).mid"),
    ("solo-keys", TM / "Only instruments/Keyboard.mp3",
     TM / "Only instruments/Keyboard (Keyboard).mid"),
    ("solo-synth", TM / "Only instruments/Synth.mp3",
     TM / "Only instruments/Synth (Synth Pad).mid"),
    ("waltz", TM / "Waltz of the Moon.mp3",
     TM / "Waltz of the Moon (Bass).mid"),
]

# Suno writes no TS meta anywhere; the waltz's 3/4 is user-attested.
# Its MIDI stubs hold 14 notes total, so its truth BEATS are built
# from the tempo meta (79 BPM) over the audio length instead of
# pretty_midi's note-bounded grid.
METER_TRUTH = {"waltz": 3}
# the waltz MIDI stubs (14 notes, gaps of 12.0 s and 5.3 s that fit
# no bar length, nominal tempo 79 vs the render) are unusable as
# TIMING truth — the track scores ONLY the meter metric
METER_ONLY = {"waltz"}
SPARSE_TRUTH = {"waltz"}

# frozen MIDI->audio offsets, seconds (onset xcorr, 2026-08-31)
OFFSETS: dict[str, float] = {
    "fulgrim": 0.145, "hero": 0.070, "loken": 0.245,
    "solo-bass": 0.120, "solo-guitar": 0.105,
    "solo-keys": 0.135, "solo-synth": 0.090,
}

TOL = 0.070


def truth_of(name: str, mid: Path, audio: Path):
    import pretty_midi

    pm = pretty_midi.PrettyMIDI(str(mid))
    ts = pm.time_signature_changes
    meter = METER_TRUTH.get(
        name, ts[0].numerator if ts else 4)   # Suno writes no TS meta
    if name in SPARSE_TRUTH:
        import librosa
        _t, tempi = pm.get_tempo_changes()
        step = 60.0 / float(tempi[0])
        dur = librosa.get_duration(path=str(audio))
        beats, x = [], 0.0
        while x < dur:
            beats.append(x)
            x += step
        return meter, beats[0::meter], beats
    return meter, list(pm.get_downbeats()), list(pm.get_beats())


def f1_times(est: list[float], truth: list[float],
             tol: float = TOL) -> float:
    if not est or not truth:
        return 0.0
    used = set()
    hits = 0
    for e in est:
        best = None
        for i, t in enumerate(truth):
            if i in used or abs(t - e) > tol:
                continue
            if best is None or abs(t - e) < abs(truth[best] - e):
                best = i
        if best is not None:
            used.add(best)
            hits += 1
    p = hits / len(est)
    r = hits / len(truth)
    return 2 * p * r / (p + r) if p + r else 0.0


def onset_offset(audio: Path, mid: Path, work: Path) -> float:
    """MIDI->audio offset: cross-correlate the truth onset train with
    the audio onset envelope (cached). Unique within +-0.6 s."""
    import json

    import librosa
    import numpy as np
    import pretty_midi

    cache = work / "oenv.json"
    if cache.exists():
        data = json.loads(cache.read_text())
        oenv, hop_s = np.array(data["oenv"]), data["hop_s"]
    else:
        y, sr = librosa.load(str(audio), sr=22050, mono=True)
        oenv = librosa.onset.onset_strength(y=y, sr=sr)
        hop_s = 512 / 22050
        work.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"oenv": [float(v) for v in oenv],
                                     "hop_s": hop_s}))
    pm = pretty_midi.PrettyMIDI(str(mid))
    starts = sorted(n.start for inst in pm.instruments
                    for n in inst.notes)
    best = (0.0, -1.0)
    for shift in np.arange(-0.6, 0.601, 0.005):
        idx = np.array([(s + shift) / hop_s for s in starts])
        idx = idx[(idx >= 0) & (idx < len(oenv) - 1)].astype(int)
        score = float(oenv[idx].mean()) if len(idx) else 0.0
        if score > best[1]:
            best = (float(shift), score)
    return best[0]


def est_of(audio: Path, work: Path, fresh: bool = False):
    """Current pipeline's beat grid + its implied downbeats/meter.
    The grid is CACHED per track: MPS separation is not bit-stable, so
    a fresh analyze wiggles the beats run to run — a stand must be
    deterministic (use --fresh to re-measure deliberately)."""
    import json

    cache = work / "beats.json"
    if cache.exists() and not fresh:
        beats = json.loads(cache.read_text())
    else:
        from tabforge.pipeline import run_analyze
        analyzed = run_analyze(audio, work, progress=lambda s, m: None)
        beats = analyzed.beats
        work.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(beats))
    return 4, beats[0::4], beats           # beats_per_measure=4, phase
                                           # from the first grid beat


def est_of_madmom(audio: Path, work: Path, use_drum_stem: bool = False,
                  tempo_informed: bool = False):
    """madmom RNN+DBN downbeats, beats_per_bar candidates [3, 4].
    Cached per track. Variants: the demucs DRUM STEM as input, and/or
    the DBN constrained to OUR calibrated tempo +-10% (madmom left
    free octave-flipped Hero to 0.17 beat F1)."""
    import json

    tag = "madmom" + ("_drums" if use_drum_stem else "")         + ("_tempo" if tempo_informed else "")
    cache = work / f"{tag}.json"
    if cache.exists():
        rows = json.loads(cache.read_text())
    else:
        import librosa
        from madmom.audio.signal import Signal
        from madmom.features.downbeats import (
            DBNDownBeatTrackingProcessor, RNNDownBeatProcessor)

        src = audio
        if use_drum_stem:
            hits = list(work.rglob("drums.wav"))
            if hits:
                src = hits[0]
        kwargs = {}
        if tempo_informed:
            beats_cache = work / "beats.json"
            if beats_cache.exists():
                import numpy as np
                b = json.loads(beats_cache.read_text())
                bpm = 60.0 / float(np.median(np.diff(b)))
                kwargs = {"min_bpm": bpm * 0.9, "max_bpm": bpm * 1.1}
        y, sr = librosa.load(str(src), sr=44100, mono=True)
        act = RNNDownBeatProcessor()(Signal(y, sample_rate=sr))
        res = DBNDownBeatTrackingProcessor(beats_per_bar=[3, 4],
                                           fps=100, **kwargs)(act)
        rows = [[float(a), int(b)] for a, b in res]
        work.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(rows))
    beats = [t for t, _b in rows]
    downs = [t for t, b in rows if b == 1]
    from collections import Counter
    bar_len = Counter(b for _t, b in rows).most_common(1)
    meter = max(b for _t, b in rows) if rows else 4
    return meter, downs, beats


def est_of_hybrid(audio: Path, work: Path, fresh: bool = False):
    """OUR beat grid (stronger: 0.61 vs madmom's 0.43 beat F1) with
    the bar PHASE elected by madmom's downbeat votes: for each of the
    4 possible bar phases of our grid, count madmom downbeats landing
    on that phase's beats (70 ms); argmax wins. Meter itself stays 4
    until the corpus can test otherwise."""
    _m, _d, beats = est_of(audio, work, fresh)
    _mm, mm_downs, _mb = est_of_madmom(audio, work,
                                       tempo_informed=True)
    best_phase, best_votes = 0, -1
    for phase in range(4):
        grid = beats[phase::4]
        votes = 0
        for d in mm_downs:
            if any(abs(d - g) <= 0.07 for g in grid):
                votes += 1
        if votes > best_votes:
            best_phase, best_votes = phase, votes
    return 4, beats[best_phase::4], beats


def est_of_beatnet(audio: Path, work: Path, fresh: bool = False):
    """BeatNet (joint beat/downbeat, offline DBN mode; the realtime
    pyaudio dependency is stubbed out — eval-only)."""
    import json

    cache = work / "beatnet.json"
    if cache.exists():
        rows = json.loads(cache.read_text())
    else:
        from BeatNet.BeatNet import BeatNet
        est = BeatNet(1, mode="offline", inference_model="DBN",
                      plot=[], thread=False)
        out = est.process(str(audio))
        rows = [[float(a), int(b)] for a, b in out]
        work.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(rows))
    beats = [t for t, _b in rows]
    downs = [t for t, b in rows if b == 1]
    meter = max((b for _t, b in rows), default=4)
    return meter, downs, beats


def est_of_accent(audio: Path, work: Path, fresh: bool = False):
    """OUR grid; bar phase elected by ACCENTS: of the 4 possible
    phases, the one whose beats carry the strongest mean onset
    envelope (cached from the offset calibration) wins."""
    import json

    import numpy as np

    _m, _d, beats = est_of(audio, work, fresh)
    data = json.loads((work / "oenv.json").read_text())
    oenv, hop_s = np.array(data["oenv"]), data["hop_s"]

    def strength(times):
        idx = np.array([t / hop_s for t in times])
        idx = idx[(idx >= 0) & (idx < len(oenv) - 1)].astype(int)
        return float(oenv[idx].mean()) if len(idx) else 0.0

    best = max(range(4), key=lambda ph: strength(beats[ph::4]))
    return 4, beats[best::4], beats


def est_of_harmonic(audio: Path, work: Path, fresh: bool = False):
    """OUR grid; bar phase elected by HARMONIC RHYTHM: chords change
    on bar lines, so the phase whose bar boundaries carry the largest
    beat-to-beat chroma change wins (beat-synchronous chroma cached)."""
    import json

    import numpy as np

    _m, _d, beats = est_of(audio, work, fresh)
    cache = work / "chroma_sync.json"
    if cache.exists():
        chroma = np.array(json.loads(cache.read_text()))
    else:
        import librosa
        y, sr = librosa.load(str(audio), sr=22050, mono=True)
        C = librosa.feature.chroma_cqt(y=y, sr=sr)
        frames = librosa.time_to_frames(beats, sr=sr)
        frames = np.clip(frames, 0, C.shape[1] - 1)
        sync = librosa.util.sync(C, frames, aggregate=np.median)
        chroma = sync.T[:len(beats)]
        chroma = chroma / (np.linalg.norm(chroma, axis=1,
                                          keepdims=True) + 1e-9)
        cache.write_text(json.dumps(chroma.tolist()))

    # chroma distance between consecutive beats; a bar boundary at
    # beat i means change(i-1 -> i) is large
    change = np.zeros(len(beats))
    for i in range(1, min(len(beats), len(chroma))):
        change[i] = 1.0 - float(np.dot(chroma[i - 1], chroma[i]))
    # librosa.util.sync segments are offset by one against the beat
    # list (segment i spans up to frame i), so the change landing "at"
    # segment i marks the bar line at beat i-1 — score shifted
    best = max(range(4),
               key=lambda ph: float(np.mean(change[ph + 1::4])
                                    if len(change[ph + 1::4]) else 0))
    return 4, beats[best::4], beats


def _grid_note_fit(beats, onsets):
    import numpy as np
    b = np.array(beats)
    slots = []
    for a, bb in zip(b, b[1:]):
        for k in range(4):
            slots.append(a + (bb - a) * k / 4)
    slots = np.array(slots)
    step16 = float(np.median(np.diff(slots)))
    on = np.array([o for o in onsets if slots[0] < o < slots[-1]])
    if not len(on):
        return 1e9
    best = 1e9
    for sh in np.arange(-0.08, 0.081, 0.01):
        idx = np.clip(np.searchsorted(slots, on + sh), 1, len(slots) - 1)
        d = np.minimum(np.abs(on + sh - slots[idx - 1]),
                       np.abs(on + sh - slots[idx]))
        best = min(best, float(np.mean(d)) / step16)
    return best


def est_of_ensemble(audio: Path, work: Path, fresh: bool = False):
    """71 v2: our grid vs madmom-with-our-tempo, selected by which
    grid explains the transcribed notes better — switching only on a
    DECISIVE advantage (fit < 0.55x ours; conservative because the
    margins are noisy — see eval.md). Phase by harmonic rhythm."""
    import json
    import sys as _sys

    _m, _d, ours = est_of(audio, work, fresh)
    beats = ours
    mus = work / "muscriptor.mid"
    try:
        _mm_m, _mm_d, mm = est_of_madmom(audio, work,
                                         tempo_informed=True)
        if mus.exists():
            _root = str(Path(__file__).resolve().parent.parent / "src")
            if _root not in _sys.path:
                _sys.path.insert(0, _root)
            from tabforge.audio.arbiter import mt3_card_notes
            notes = []
            for card in ("guitar", "bass", "piano", "other"):
                notes += [n.start
                          for n in (mt3_card_notes(mus, card) or [])]
            notes = sorted(set(round(x, 3) for x in notes))
            if len(notes) > 50 and                     _grid_note_fit(mm, notes)                     < 0.55 * _grid_note_fit(ours, notes):
                beats = mm
    except Exception:  # noqa: BLE001
        pass
    # harmonic phase on the winning grid
    import numpy as np
    data = json.loads((work / "chroma_sync.json").read_text())         if (work / "chroma_sync.json").exists() and beats is ours else None
    if data is None:
        import librosa
        y, sr = librosa.load(str(audio), sr=22050, mono=True)
        C = librosa.feature.chroma_cqt(y=y, sr=sr)
        frames = np.clip(librosa.time_to_frames(beats, sr=sr),
                         0, C.shape[1] - 1)
        sync = librosa.util.sync(C, frames, aggregate=np.median)
        chroma = sync.T[:len(beats)]
        chroma = chroma / (np.linalg.norm(chroma, axis=1,
                                          keepdims=True) + 1e-9)
    else:
        chroma = np.array(data)
    change = np.zeros(len(beats))
    for i in range(1, min(len(beats), len(chroma))):
        change[i] = 1.0 - float(np.dot(chroma[i - 1], chroma[i]))
    ph = max(range(4), key=lambda k: float(np.mean(change[k + 1::4])
                                           if len(change[k + 1::4])
                                           else 0.0))
    return 4, beats[ph::4], beats


ENGINES = {
    "current": lambda audio, work, fresh: est_of(audio, work, fresh),
    "ensemble": est_of_ensemble,
    "harmonic": est_of_harmonic,
    "accent": est_of_accent,
    "beatnet": est_of_beatnet,
    "hybrid": est_of_hybrid,
    "madmom": lambda audio, work, fresh: est_of_madmom(audio, work),
    "madmom-drums": lambda audio, work, fresh:
        est_of_madmom(audio, work, use_drum_stem=True),
    "madmom-tempo": lambda audio, work, fresh:
        est_of_madmom(audio, work, tempo_informed=True),
    "madmom-drums-tempo": lambda audio, work, fresh:
        est_of_madmom(audio, work, use_drum_stem=True,
                      tempo_informed=True),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--calibrate", action="store_true",
                    help="sweep per-track offsets and print them")
    ap.add_argument("--engine", default="current",
                    choices=sorted(ENGINES))
    ap.add_argument("--work", type=Path,
                    default=ROOT / "eval_out" / "meter")
    ap.add_argument("--fresh", action="store_true",
                    help="re-run analyze instead of the cached grid")
    args = ap.parse_args()

    import numpy as np

    rows = []
    for name, audio, mid in TRACKS:
        t_meter, t_down, t_beats = truth_of(name, mid, audio)
        e_meter, e_down, e_beats = ENGINES[args.engine](
            audio, args.work / name, args.fresh)

        if args.calibrate or name not in OFFSETS:
            off = onset_offset(audio, mid, args.work / name)
            if args.calibrate:
                print(f"  {name}: offset {off:+.3f}s (onset xcorr)")
        else:
            off = OFFSETS[name]

        td = [t + off for t in t_down]
        tb = [t + off for t in t_beats]
        if name in METER_ONLY:
            rows.append((name, t_meter, e_meter == t_meter, None, None))
        else:
            rows.append((name, t_meter, e_meter == t_meter,
                         f1_times(e_beats, tb), f1_times(e_down, td)))

    print(f"\n[{args.engine}]")
    print(f"{'track':12s} meter  meter-ok  beatF1  downbeatF1")
    for name, m, ok, bf, df in rows:
        cells = ("  (meter-only)" if bf is None
                 else f"    {bf:5.2f}   {df:5.2f}")
        print(f"{name:12s} {m}/4    {'yes' if ok else 'NO ':>3s}{cells}")
    timed = [r for r in rows if r[3] is not None]
    print(f"{'MEAN':12s}              "
          f"{np.mean([r[3] for r in timed]):5.2f}   "
          f"{np.mean([r[4] for r in timed]):5.2f}   "
          f"meter acc {np.mean([r[2] for r in rows]):.2f}")


if __name__ == "__main__":
    main()
