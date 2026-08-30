"""
Task 67: tune the Viterbi fingering costs against GuitarSet string
assignment (the 0.598 baseline from task 65).

Protocol (user-dictated):
- Split BY PLAYER, not by track (style leaks across a player's takes):
  players 00-03 train, 04-05 test. Tune on train, report test ONCE.
- Truth notes in, string agreement out — the LAYOUT is tuned, not the
  transcription (transcription noise would drown the signal).
- Coordinate descent over the 6 cost coefficients, coarse grids,
  repeated until a full round yields no improvement.
- Error histograms before/after decide whether tuning suffices or the
  cost function needs a new term.

    .venv/bin/python scripts/tune_viterbi.py --tune
    .venv/bin/python scripts/tune_viterbi.py --report  (test set, final)
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from eval_guitarset import STANDARD, load_truth  # noqa: E402

TRAIN_PLAYERS = ("00", "01", "02", "03")
TEST_PLAYERS = ("04", "05")

# the tuned axes and their sweep grids — deliberately coarse; the
# hypothesis from task 65 is that high_fret_penalty is the villain
GRIDS = {
    "high_fret_penalty": (0.0, 0.005, 0.01, 0.015, 0.03, 0.05),
    "move_penalty": (0.8, 1.2, 1.6, 2.0, 2.6, 3.4),
    "open_string_bonus": (0.0, 0.03, 0.07, 0.15, 0.35),
    "stretch_penalty": (0.3, 0.45, 0.6, 0.9, 1.2),
    "string_change_penalty": (0.0, 0.02, 0.05, 0.1),
    "reach": (3, 4),
}


def player_files(root: Path, players) -> list[Path]:
    return [f for f in sorted((root / "annotation").glob("*.jams"))
            if f.stem[:2] in players]


def load_all(files):
    """Parse every jams once; tuning re-runs only the Viterbi."""
    from tabforge.core.fretboard import NoteEvent

    out = []
    for f in files:
        truth = load_truth(f)
        if not truth:
            continue
        events = [NoteEvent(p, s, d) for p, s, d, _ in truth]
        out.append((f.stem, events, truth))
    return out


def score(dataset, cfg) -> tuple[float, Counter, dict]:
    """String agreement of assign_tab against the humans."""
    from tabforge.core.fretboard import assign_tab

    total = agree = 0
    confusion: Counter = Counter()
    detail = {"open": [0, 0], "fretted": [0, 0]}
    for _name, events, truth in dataset:
        shapes = assign_tab(events, cfg)
        assigned = {}
        for shape in shapes:
            for pl in shape.placements:
                assigned[id(pl.note)] = pl.string
        for ev, (p, s, d, true_string) in zip(events, truth):
            ours = assigned.get(id(ev))
            if ours is None:
                continue
            total += 1
            kind = "open" if p == STANDARD[true_string] else "fretted"
            detail[kind][1] += 1
            if ours == true_string:
                agree += 1
                detail[kind][0] += 1
            else:
                confusion[(true_string, ours)] += 1
    return agree / max(total, 1), confusion, detail


def show(tag, acc, confusion, detail) -> None:
    names = ("E", "A", "D", "G", "B", "e")
    o, f = detail["open"], detail["fretted"]
    print(f"{tag}: {acc:.4f}  open {o[0]}/{o[1]}={o[0]/max(o[1],1):.3f}  "
          f"fretted {f[0]}/{f[1]}={f[0]/max(f[1],1):.3f}", flush=True)
    for (t, ours), c in confusion.most_common(6):
        print(f"    {names[t]} -> {names[ours]}: {c}", flush=True)


def tune(root: Path, **overrides) -> None:
    from tabforge.core import TUNINGS, TabConfig

    dataset = load_all(player_files(root, TRAIN_PLAYERS))
    n_notes = sum(len(t) for _, _, t in dataset)
    print(f"train: {len(dataset)} excerpts, {n_notes} notes", flush=True)

    cfg = replace(TabConfig(tuning=TUNINGS["standard"]), **overrides)
    t0 = time.time()
    best_acc, conf, det = score(dataset, cfg)
    print(f"one pass: {time.time()-t0:.0f}s", flush=True)
    show("baseline(train)", best_acc, conf, det)

    improved = True
    rounds = 0
    while improved and rounds < 5:
        improved = False
        rounds += 1
        for axis, grid in GRIDS.items():
            cur = getattr(cfg, axis)
            for v in grid:
                if v == cur:
                    continue
                acc, _, _ = score(dataset, replace(cfg, **{axis: v}))
                print(f"  {axis}={v}: {acc:.4f}", flush=True)
                if acc > best_acc + 1e-4:
                    best_acc = acc
                    cfg = replace(cfg, **{axis: v})
                    improved = True
            print(f"round {rounds}: {axis} -> {getattr(cfg, axis)} "
                  f"(train {best_acc:.4f})", flush=True)
    print("\nBEST(train):", best_acc, flush=True)
    for axis in GRIDS:
        print(f"  {axis} = {getattr(cfg, axis)}", flush=True)
    acc, conf, det = score(dataset, cfg)
    show("tuned(train)", acc, conf, det)


def report(root: Path, **overrides) -> None:
    """The one look at the test set — run AFTER tuning settles."""
    from tabforge.core import TUNINGS, TabConfig

    dataset = load_all(player_files(root, TEST_PLAYERS))
    base = TabConfig(tuning=TUNINGS["standard"])
    acc, conf, det = score(dataset, base)
    show("test/default-weights", acc, conf, det)
    if overrides:
        tuned = replace(base, **overrides)
        acc, conf, det = score(dataset, tuned)
        show("test/tuned-weights", acc, conf, det)


def main() -> None:
    ap = argparse.ArgumentParser()
    default_root = Path(
        "/private/tmp/claude-501/-Users-rc-Desktop-tabforge/"
        "d62a89ba-78b3-4d8f-b883-d826ab37e798/scratchpad/guitarset")
    ap.add_argument("--root", type=Path, default=default_root)
    ap.add_argument("--tune", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--set", nargs="*", default=[],
                    metavar="axis=value",
                    help="overrides for --report, e.g. move_penalty=0.8")
    args = ap.parse_args()
    overrides = {}
    for kv in args.set:
        k, v = kv.split("=")
        overrides[k] = int(v) if k == "reach" else float(v)
    if args.tune:
        tune(args.root, **overrides)
    if args.report:
        report(args.root, **overrides)


if __name__ == "__main__":
    main()
