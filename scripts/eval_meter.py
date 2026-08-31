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
]

# frozen MIDI->audio offsets, seconds (onset xcorr, 2026-08-31)
OFFSETS: dict[str, float] = {
    "fulgrim": 0.145, "hero": 0.070, "loken": 0.245,
    "solo-bass": 0.120, "solo-guitar": 0.105,
    "solo-keys": 0.135, "solo-synth": 0.090,
}

TOL = 0.070


def truth_of(mid: Path):
    import pretty_midi

    pm = pretty_midi.PrettyMIDI(str(mid))
    ts = pm.time_signature_changes
    meter = ts[0].numerator if ts else 4      # Suno writes no TS meta
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--calibrate", action="store_true",
                    help="sweep per-track offsets and print them")
    ap.add_argument("--work", type=Path,
                    default=ROOT / "eval_out" / "meter")
    ap.add_argument("--fresh", action="store_true",
                    help="re-run analyze instead of the cached grid")
    args = ap.parse_args()

    import numpy as np

    rows = []
    for name, audio, mid in TRACKS:
        t_meter, t_down, t_beats = truth_of(mid)
        e_meter, e_down, e_beats = est_of(audio, args.work / name, args.fresh)

        if args.calibrate or name not in OFFSETS:
            off = onset_offset(audio, mid, args.work / name)
            if args.calibrate:
                print(f"  {name}: offset {off:+.3f}s (onset xcorr)")
        else:
            off = OFFSETS[name]

        td = [t + off for t in t_down]
        tb = [t + off for t in t_beats]
        rows.append((name, t_meter, e_meter == t_meter,
                     f1_times(e_beats, tb), f1_times(e_down, td)))

    print(f"\n{'track':12s} meter  meter-ok  beatF1  downbeatF1")
    for name, m, ok, bf, df in rows:
        print(f"{name:12s} {m}/4    {'yes' if ok else 'NO ':>3s}    "
              f"{bf:5.2f}   {df:5.2f}")
    print(f"{'MEAN':12s}              "
          f"{np.mean([r[3] for r in rows]):5.2f}   "
          f"{np.mean([r[4] for r in rows]):5.2f}   "
          f"meter acc {np.mean([r[2] for r in rows]):.2f}")


if __name__ == "__main__":
    main()
