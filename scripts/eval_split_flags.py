"""Casey split ruler (calibration session 1): score split_lead_rhythm
against the user's 22 assignment flags — each flag says "the notes
sounding HERE in this part belong to the other part".

    python scripts/eval_split_flags.py

A flag scores correct when, after a fresh split of the merged guitar
notes, the majority of notes within +-0.6 s of the flagged moment
land in the part the user asked for.
"""
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

D = ROOT / "calibration/2026-09-01-session1"

# flags that are about ASSIGNMENT (bar/qticks -> which part is right);
# text tells the direction: flagged IN rhythm + "лид" => lead, and
# vice versa
ASSIGN = {
    "лид", "соло", "высок",            # -> lead
    "ритм",                            # -> rhythm (when flagged in lead)
}


def flag_targets():
    flags = json.loads((D / "flags.json").read_text())
    out = []
    for f in flags:
        txt = f["text"].lower()
        if "легато" in txt or "диссонанс" in txt or "паузы" in txt:
            continue                    # other cases
        if f["part"] == "guitar_rhythm" and ("лид" in txt or "соло" in txt
                                             or "высок" in txt):
            out.append((f["qticks"], "lead", f["text"]))
        elif f["part"] == "guitar_lead" and "ритм" in txt:
            out.append((f["qticks"], "rhythm", f["text"]))
    return out


def main() -> None:
    from tabforge.core.partition import split_lead_rhythm
    from tabforge.pipeline import _revive_notes

    meta = json.loads(zipfile.ZipFile(D / "project.tabforge")
                      .read("tabforge-project.json"))
    beats = meta["beats"]
    st = json.loads((D / "parts.json").read_text())
    merged = []
    for name in ("guitar_lead", "guitar_rhythm"):
        merged.extend(_revive_notes(st[name]))
    merged.sort(key=lambda n: n.start)

    res = split_lead_rhythm(merged)
    if res is None:
        print("split=None — every assignment flag fails")
        return
    lead, rhythm = res
    lead_ids = {id(n) for n in lead}
    old_lead_ids = {id(n) for n in merged
                    if any(abs(n.start - m["start"]) < 1e-6
                           and n.pitch == m["pitch"]
                           for m in st["guitar_lead"]["notes"])}
    pivot = sorted(n.pitch for n in merged)[len(merged) // 2]

    def t_of(q):
        x = q / 960
        i = min(int(x), len(beats) - 2)
        return beats[i] + (x - i) * (beats[i + 1] - beats[i])

    ok = 0
    rows = []
    for q, want, txt in flag_targets():
        t0 = t_of(q)
        if want == "lead":
            # the flag targets the SOLO voice: the high notes here
            near = [n for n in merged if abs(n.start - t0) <= 0.6
                    and n.pitch >= pivot + 10]
        else:
            # flagged while viewing the (old) lead part: those notes
            near = [n for n in merged if abs(n.start - t0) <= 0.6
                    and id(n) in old_lead_ids]
        if not near:
            rows.append((q, want, "no notes", txt))
            continue
        in_lead = sum(1 for n in near if id(n) in lead_ids)
        got = "lead" if in_lead * 2 >= len(near) else "rhythm"
        good = got == want
        ok += good
        rows.append((q, want, ("OK" if good else f"got {got}"), txt))
    for q, want, verdict, txt in rows:
        print(f"  q={q:6d} want={want:6s} {verdict:10s} | {txt[:40]}")
    n = len(rows)
    print(f"\nFLAG SCORE: {ok}/{n} = {ok / max(1, n):.2f}")


if __name__ == "__main__":
    main()
