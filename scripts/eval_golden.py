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


def _match_shift(ref: list, est: list, span: float = 0.5,
                 bin_s: float = 0.01, tol: float = 0.05) -> float:
    """Best est->truth time shift: the delta that lets the most
    SAME-PITCH (ref, est) onset pairs match within tol. A plain onset
    cross-correlation is quasi-periodic on grid-quantized material
    (peaks a beat apart — Fulgrim picked −0.49 s instead of −0.13);
    conditioning the deltas on pitch pins the true offset. Pitch CLASS
    (mod 12), so an octave-convention gap (mono bass) can't starve the
    histogram."""
    by_pitch: dict[int, list[float]] = {}
    for p, s, _ in est:
        by_pitch.setdefault(p % 12, []).append(s)
    k = int(span / bin_s)
    hist = np.zeros(2 * k + 1)
    for p, s, _ in ref:
        for es in by_pitch.get(p % 12, ()):
            d = round((es - s) / bin_s)
            if -k <= d <= k:
                hist[d + k] += 1
    if not hist.any():
        return 0.0, 0.0
    w = int(tol / bin_s)
    win = np.convolve(hist, np.ones(2 * w + 1), mode="same")
    shift = -(int(np.argmax(win)) - k) * bin_s
    return (shift if abs(shift) > 0.02 else 0.0), float(win.max())

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
            # dead notes are rhythm marks, not pitch claims (recitative
            # vocals) — they must not count against precision
            est[part] = [(n["pitch"], n["start"], n["duration"])
                         for n in p["notes"] if not n.get("dead")]
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


def _global_shift(truth: dict, est: dict, home_map: dict) -> float:
    """Global est->truth shift: WEIGHTED median of per-instrument
    shifts, weight = the instrument's peak match count. A pooled
    histogram is fragile — junk est on a quiet stem drags the peak to
    a spurious offset (Fulgrim: −0.49 s); an unweighted median lets
    noise instruments (synth F1 0.04) outvote the band."""
    pairs = []
    for inst, ref in truth.items():
        home = home_map.get(inst, (inst,))
        est_home = [n for h in home for n in est.get(h, [])]
        if len(ref) >= 50 and len(est_home) >= 50:
            pairs.append(_match_shift(ref, est_home))
    if not pairs:   # tiny corpus: fall back to whatever exists
        pairs = [_match_shift(
            [n for notes in truth.values() for n in notes],
            [n for notes in est.values() for n in notes])]
    pairs.sort()
    total = sum(w for _, w in pairs)
    if total <= 0:
        return 0.0
    acc = 0.0
    shift = pairs[-1][0]
    for s, w in pairs:
        acc += w
        if acc >= total / 2:
            shift = s
            break
    return shift if abs(shift) > 0.02 else 0.0


def _best_alignment(ref: list, est: list, tol: float,
                    span: float = 0.3) -> tuple[float, int]:
    """Per-instrument alignment = argmax of strict F1 over (time
    offset, octave convention). Both are REAL per-file artifacts: Suno
    exports every instrument MIDI separately (Hero drums ~0, guitar
    −65 ms, vocals −235 ms), and logs synth bass an octave above the
    acoustic fundamental. Searched JOINTLY (a wrong octave zeroes F1
    at every shift, hiding the true offset). Coarse 20 ms grid, then a
    5 ms refine; every system on the stand gets the same favor."""
    pad = span + 0.02   # keep every interval positive at any offset
    est_pad = [(p, s + pad, dur) for p, s, dur in est]

    def f_at(d: float, est_k: list) -> float:
        ref_d = [(p, s + d + pad, dur) for p, s, dur in ref]
        return _prf(ref_d, est_k, tol)[2]

    best_f, best_d, best_k = -1.0, 0.0, 0
    for k in (0, -12, 12):
        est_k = ([(p + k, s, dur) for p, s, dur in est_pad]
                 if k else est_pad)
        for d in np.arange(-span, span + 0.01, 0.02):
            f = f_at(float(d), est_k)
            if f > best_f:
                best_f, best_d, best_k = f, float(d), k
    est_k = ([(p + best_k, s, dur) for p, s, dur in est_pad]
             if best_k else est_pad)
    for d in np.arange(best_d - 0.015, best_d + 0.016, 0.005):
        f = f_at(float(d), est_k)
        if f > best_f:
            best_f, best_d = f, float(d)
    return (-best_d if abs(best_d) > 0.02 else 0.0), best_k


def score(name: str, truth: dict, est: dict, home_map: dict,
          align: bool = True, tol: float = ONSET_TOL) -> list[dict]:
    glob = _global_shift(truth, est, home_map) if align else 0.0
    rows = []
    for inst, ref_raw in truth.items():
        home = home_map.get(inst, (inst,))
        est_home = [n for h in home for n in est.get(h, [])]
        shift, k_best = glob, 0
        if align and len(ref_raw) >= 50 and len(est_home) >= 50:
            shift, k_best = _best_alignment(ref_raw, est_home, tol)
        if shift or k_best:
            print(f"  [align] {inst}: est shift {shift:+.3f}s, "
                  f"octave {k_best:+d}", flush=True)
        # shift the TRUTH the other way; pad BOTH sides into positive
        # territory (mir_eval refuses negative interval starts)
        pad = max(0.0, -min((s - shift for _, s, _ in ref_raw),
                            default=0.0))
        ref = [(p, s - shift + pad, d) for p, s, d in ref_raw]
        est_home = [(p, s + pad, d) for p, s, d in est_home]
        foreign = [(p, s + pad, d) for k, notes in est.items()
                   if k not in home for p, s, d in notes]
        if k_best:
            est_home = [(pp + k_best, ss, dd) for pp, ss, dd in est_home]
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
