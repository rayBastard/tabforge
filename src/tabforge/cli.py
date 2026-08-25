"""CLI: python -m tabforge song.mp3 --stems guitar bass"""
from __future__ import annotations

import argparse
from pathlib import Path

from .core.fretboard import TUNINGS
from .pipeline import PipelineOptions, run_pipeline


def main() -> None:
    ap = argparse.ArgumentParser(prog="tabforge")
    ap.add_argument("audio", type=Path)
    ap.add_argument("--out", type=Path, default=Path("./out"))
    ap.add_argument("--stems", nargs="*", default=["guitar", "bass"])
    ap.add_argument("--tuning", default="standard", choices=sorted(TUNINGS))
    ap.add_argument("--subdivision", type=int, default=4)
    ap.add_argument("--quantize", type=float, default=0.9)
    ap.add_argument("--split-guitars", action="store_true",
                    help="split the guitar stem into lead and rhythm parts")
    args = ap.parse_args()

    opts = PipelineOptions(
        stems=tuple(args.stems),
        tuning=args.tuning,
        subdivision=args.subdivision,
        quantize_strength=args.quantize,
        separate=args.stems != ["mix"],
        split_guitars=args.split_guitars,
    )
    results = run_pipeline(args.audio, args.out, opts,
                           progress=lambda st, msg: print(f"[{st}] {msg}"))
    for r in results:
        print(f"\n=== {r.stem}: {r.note_count} notes, {r.bpm:.0f} BPM, {r.key} ===")
        print(r.ascii_tab[:800])
    print(f"\nDone: {args.out.resolve()}")


if __name__ == "__main__":
    main()
