"""
The GOLDEN stand: real tracks, real per-instrument MIDI ground truth.

The user's corpus lives in "Tracks and midi/": <name>.mp3 plus
"<name> (Instrument).mid" files (Suno's own exports, aligned with the
audio). This script scores the full pipeline — and, given --mt3-midi
files, the YourMT3+ experiment — against that truth with the same
metrics as the synthetic stand.

    .venv/bin/python scripts/eval_golden.py                # pipeline
    .venv/bin/python scripts/eval_golden.py --separator roformer
    .venv/bin/python scripts/eval_golden.py --mt3-dir <model_output>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

# Suno writes impossible key signatures (9 sharps); mido must shrug
from mido.midifiles import meta as _mido_meta  # noqa: E402

_orig_keys = dict(_mido_meta._key_signature_decode)


class _TolerantKeys(dict):
    def __missing__(self, key):
        return "C"


_mido_meta._key_signature_decode = _TolerantKeys(_orig_keys)

from eval_transcription import _octave_error_rate, _hz  # noqa: E402

GOLDEN_DIR = ROOT / "Tracks and midi"
# The honest ruler (task 52): 50 ms strict onset tolerance, applied
# AFTER global cross-correlation alignment (Suno truth MIDI is shifted
# vs the rendered audio — Loken by a uniform −240 ms).
ONSET_TOL = 0.05
PITCH_ONLY_TOL = 0.5


def _prf(ref: list, est: list, tol: float) -> tuple[float, float, float]:
    import mir_eval

    if not ref or not est:
        return 0.0, 0.0, 0.0
    ref_i = np.array([[s, s + d] for _, s, d in ref])
    ref_p = np.array([_hz(p) for p, _, _ in ref])
    est_i = np.array([[s, s + d] for _, s, d in est])
    est_p = np.array([_hz(p) for p, _, _ in est])
    p, r, f, _ = mir_eval.transcription.precision_recall_f1_overlap(
        ref_i, ref_p, est_i, est_p,
        onset_tolerance=tol, offset_ratio=None)
    return p, r, f


def _global_shift(truth: dict, est: dict,
                  span: float = 0.5, bin_s: float = 0.01) -> float:
    """Best global est->truth time shift by onset cross-correlation,
    pooled over all instruments. Returns 0 when |shift| <= 20 ms."""
    ref_on = [s for notes in truth.values() for _, s, _ in notes]
    est_on = [s for notes in est.values() for _, s, _ in notes]
    if not ref_on or not est_on:
        return 0.0
    n = int((max(max(ref_on), max(est_on)) + 1) / bin_s) + 1
    a = np.zeros(n)
    b = np.zeros(n)
    for s in ref_on:
        a[int(s / bin_s)] += 1
    for s in est_on:
        b[int(s / bin_s)] += 1
    k = int(span / bin_s)
    best_v, best_d = -1.0, 0
    for d in range(-k, k + 1):
        v = np.dot(a[d:], b[:n - d]) if d >= 0 else np.dot(a[:n + d], b[-d:])
        if v > best_v:
            best_v, best_d = v, d
    shift = best_d * bin_s
    return shift if abs(shift) > 0.02 else 0.0

HOME = {"guitar": ("guitar", "guitar_lead", "guitar_rhythm"),
        "bass": ("bass",),
        "vocals": ("vocals",),
        "synth": ("other", "piano", "piano_left"),
        "piano": ("piano", "piano_left"),
        "drums": ("drums",)}

# MT3 program ranges -> our classes (mirrors scripts/mt3_experiment)
def _mt3_class(program: int, is_drum: bool) -> str:
    if is_drum:
        return "drums"
    if program <= 7:
        return "piano"
    if 24 <= program <= 31:
        return "guitar"
    if 32 <= program <= 39:
        return "bass"
    if 52 <= program <= 54:
        return "vocals"
    if 80 <= program <= 95:
        return "synth"
    return "other"


MT3_HOME = {"guitar": ("guitar",), "bass": ("bass",),
            "vocals": ("vocals", "other"),
            "synth": ("synth", "piano", "other"),
            "piano": ("piano",),
            "drums": ("drums",)}


def _prefix_len(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a.lower(), b.lower()):
        if x != y:
            break
        n += 1
    return n


def load_truth(audio: Path) -> dict[str, list]:
    """MIDIs matched by common name prefix (the corpus mixes naming:
    "FulgrimUpd.wav" goes with "Fulgrim (Piano) 2.mid"); the instrument
    is the first parenthesized word."""
    import re

    import pretty_midi

    truth: dict[str, list] = {}
    for mid in sorted(GOLDEN_DIR.glob("*.mid")):
        if _prefix_len(mid.stem, audio.stem) < 5:
            continue
        groups = re.findall(r"\(([^)]+)\)", mid.stem)
        inst = next((g.strip().lower() for g in groups
                     if g.strip().isalpha()), None)
        if inst is None:
            continue
        pm = pretty_midi.PrettyMIDI(str(mid))
        notes = [(n.pitch, n.start, max(n.end - n.start, 0.05))
                 for t in pm.instruments for n in t.notes]
        truth.setdefault(inst, []).extend(notes)     # merges Guitar (1)+(2)
    return truth


def pipeline_estimates(mp3: Path, work: Path, separator: str,
                       low_pass: bool = False) -> dict:
    import json

    from tabforge.pipeline import PipelineOptions, run_pipeline

    opts = PipelineOptions(stems=("guitar", "bass", "piano", "vocals",
                                  "other", "drums"), subdivision=2,
                           separator=separator, low_pass=low_pass)
    # parts.json is a merge-on-write state file: parts from an earlier
    # run with different settings (e.g. an auto lead/rhythm split that
    # no longer triggers) would survive and be double-counted by the
    # scorer. Start every eval run from a clean state.
    stale = work / "parts.json"
    if stale.exists():
        stale.unlink()
    run_pipeline(mp3, work, opts)
    est: dict[str, list] = {}
    parts_file = work / "parts.json"
    if parts_file.exists():
        state = json.loads(parts_file.read_text())
        for part, p in state.items():
            est[part] = [(n["pitch"], n["start"], n["duration"])
                         for n in p["notes"]]
    drums_wav = next((work / "stems").rglob("drums.wav"), None)
    if drums_wav:
        from tabforge.audio.drums import transcribe_drums
        est["drums"] = [(n.pitch, n.start, n.duration)
                        for n in transcribe_drums(drums_wav)]
    return est


def mt3_estimates(midi: Path) -> dict:
    import pretty_midi

    est: dict[str, list] = {}
    pm = pretty_midi.PrettyMIDI(str(midi))
    for t in pm.instruments:
        klass = _mt3_class(t.program, t.is_drum)
        est.setdefault(klass, []).extend(
            (n.pitch, n.start, max(n.end - n.start, 0.05))
            for n in t.notes)
    return est


def score(name: str, truth: dict, est: dict, home_map: dict,
          align: bool = True, tol: float = ONSET_TOL) -> list[dict]:
    shift = _global_shift(truth, est) if align else 0.0
    if shift:
        print(f"  [align] global est shift {shift:+.3f}s", flush=True)
        # shift the TRUTH the other way: keeps every time positive
        # (mir_eval refuses negative interval starts)
        truth = {k: [(p, s - shift, d) for p, s, d in v]
                 for k, v in truth.items()}
    rows = []
    for inst, ref in truth.items():
        home = home_map.get(inst, (inst,))
        est_home = [n for h in home for n in est.get(h, [])]
        foreign = [n for k, notes in est.items()
                   if k not in home for n in notes]
        p, r, f = _prf(ref, est_home, tol)
        _, _, pf = _prf(ref, est_home, PITCH_ONLY_TOL)
        leaked = sum(
            1 for pp, ss, _ in ref
            if any(abs(es - ss) < ONSET_TOL and ep % 12 == pp % 12
                   for ep, es, _ in foreign))
        rows.append({"track": name, "inst": inst, "ref": len(ref),
                     "est": len(est_home), "p": p, "r": r, "f1": f,
                     "pf1": pf, "oct": _octave_error_rate(ref, est_home),
                     "leak": leaked / len(ref) if ref else 0.0,
                     "shift": shift})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--separator", default="demucs",
                    choices=("demucs", "roformer"))
    ap.add_argument("--mt3-dir", default=None,
                    help="score MT3 MIDI outputs from this dir instead "
                         "of running the pipeline")
    ap.add_argument("--tracks", nargs="*", default=None)
    ap.add_argument("--low-pass", action="store_true",
                    help="octave double-pass for bass/guitar/vocals")
    ap.add_argument("--tol", type=float, default=ONSET_TOL,
                    help="strict onset tolerance, s (default 0.05)")
    ap.add_argument("--no-align", action="store_true",
                    help="skip global onset cross-correlation alignment")
    ap.add_argument("--out", default=str(ROOT / "eval_out" / "golden"))
    args = ap.parse_args()

    rows = []
    audios = sorted(list(GOLDEN_DIR.glob("*.mp3"))
                    + list(GOLDEN_DIR.glob("*.wav")))
    for mp3 in audios:
        if args.tracks and mp3.stem not in args.tracks:
            continue
        print(f"=== {mp3.stem} ===", flush=True)
        truth = load_truth(mp3)
        if args.mt3_dir:
            midi = Path(args.mt3_dir) / f"{mp3.stem}.mid"
            est = mt3_estimates(midi)
            rows += score(mp3.stem, truth, est, MT3_HOME,
                          align=not args.no_align, tol=args.tol)
        else:
            tag = args.separator + ("_lp" if args.low_pass else "")
            work = Path(args.out) / tag / mp3.stem
            est = pipeline_estimates(mp3, work, args.separator,
                                     low_pass=args.low_pass)
            rows += score(mp3.stem, truth, est, HOME,
                          align=not args.no_align, tol=args.tol)

    hdr = (f"{'track':22s} {'inst':7s} {'ref':>5s} {'est':>5s} "
           f"{'P':>5s} {'R':>5s} {'F1':>5s} {'pF1':>5s} {'oct':>5s} "
           f"{'leak':>5s} {'shift':>6s}")
    print("\n" + hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r['track'][:22]:22s} {r['inst']:7s} {r['ref']:5d} "
              f"{r['est']:5d} {r['p']:5.2f} {r['r']:5.2f} {r['f1']:5.2f} "
              f"{r['pf1']:5.2f} {r['oct']:5.2f} {r['leak']:5.2f} "
              f"{r['shift']:+6.3f}")
    by: dict[str, list] = {}
    for r in rows:
        by.setdefault(r["inst"], []).append(r)
    print("-" * len(hdr))
    for inst, rs in sorted(by.items()):
        print(f"{'MEAN':22s} {inst:7s} {'':11s} "
              f"{'':5s} {'':5s} {np.mean([x['f1'] for x in rs]):5.2f} "
              f"{np.mean([x['pf1'] for x in rs]):5.2f} "
              f"{np.mean([x['oct'] for x in rs]):5.2f} "
              f"{np.mean([x['leak'] for x in rs]):5.2f}")


if __name__ == "__main__":
    main()
