"""Notation metrics (task 70 deferred these to land with 73): position
error and notation complexity of a finished run's score.

    python scripts/eval_notation.py <out_dir> [<out_dir> ...]

Reads out_dir/song/song.gp5 (complexity) and out_dir/trace.json
(position error — present when the run was made with
TABFORGE_WRITER_TRACE=<out_dir>/trace.json). Complexity = shares of
tied / dotted / tuplet beats and rests; position error = written slot
vs the raw event time on the run's own fine grid, in fine units
(grid-relative — comparable across runs of the same track).
"""
import json
import sys
from pathlib import Path

import guitarpro as gp

FINE = 24


def score_dir(out_dir: Path) -> None:
    gp5 = out_dir / "song" / "song.gp5"
    if not gp5.exists():
        print(f"[{out_dir.name}] no song/song.gp5 — skipped")
        return
    song = gp.parse(str(gp5))
    note_beats = tie = dotted = tup = rest = 0
    for trk in song.tracks:
        for m in trk.measures:
            for b in m.voices[0].beats:
                if b.status == gp.BeatStatus.rest:
                    rest += 1
                    continue
                if b.status != gp.BeatStatus.normal:
                    continue
                note_beats += 1
                if b.notes and all(n.type == gp.NoteType.tie
                                   for n in b.notes):
                    tie += 1
                if b.duration.isDotted:
                    dotted += 1
                if b.duration.tuplet.enters == 3:
                    tup += 1
    attacks = note_beats - tie
    line = (f"[{out_dir.name}] attacks={attacks} "
            f"ties={tie / max(1, note_beats):.1%} "
            f"dotted={dotted / max(1, note_beats):.1%} "
            f"tuplets={tup} rests={rest}")

    trace = out_dir / "trace.json"
    if trace.exists():
        rows = json.loads(trace.read_text())
        finals = [e for e in rows if e.get("final")]
        raws = {}
        for e in rows:
            if not e.get("final"):
                raws.setdefault((e["part"], e["raw"]), e["fine"])
        errs = []
        for e in finals:
            raw_fine = raws.get((e["part"], e["raw"]))
            if raw_fine is None:
                continue
            width = FINE // e["d"]
            written = e["m"] * 4 * FINE + e["s"] * width
            errs.append(abs(written - raw_fine))
        if errs:
            errs.sort()
            line += (f" | pos_err={sum(errs) / len(errs):.2f} fine units"
                     f" p90={errs[int(len(errs) * 0.9)]}")
    print(line)


if __name__ == "__main__":
    for d in sys.argv[1:]:
        score_dir(Path(d))
