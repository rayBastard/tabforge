"""Guitar-count / voice-split ruler (deferred block, 2026-08-31).

Measures split_lead_rhythm against the only two-guitar truth we own —
Loken's (Guitar)(1)/(2) MIDIs — plus the DECISION side: one true
guitar (solo corpus) must not split.

Voice metric: every transcribed guitar note is assigned to whichever
truth guitar has a matching note (onset within 60 ms at the best
global shift, same pitch class; notes matching both are ambiguous and
skipped). PURITY of a part = share of its assigned notes belonging to
its majority guitar; VOICE ACCURACY = consistent share under the best
part<->guitar mapping.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import eval_golden  # noqa: F401  (mido patch for Suno's 9-sharp keys)
import pretty_midi

ROOT = Path(__file__).resolve().parent.parent
LOKEN = ROOT / "eval_out/golden/demucs/Loken (The End And The Death)"
TRUTH = [ROOT / "Tracks and midi/Loken (The End And The Death) (Guitar) (1).mid",
         ROOT / "Tracks and midi/Loken (The End And The Death) (Guitar) (2).mid"]


def truth_notes(p):
    pm = pretty_midi.PrettyMIDI(str(p))
    return sorted((n.start, n.pitch)
                  for i in pm.instruments for n in i.notes)


def best_shift(ours, truths, span=0.3, step=0.025):
    def hits(shift):
        return sum(1 for t, p in ours
                   if any(abs(t + shift - tt) <= 0.06
                          and (p - pp) % 12 == 0
                          for tt, pp in truths))
    shifts = [k * step for k in range(-int(span / step),
                                      int(span / step) + 1)]
    return max(shifts, key=hits)


def assign(ours, g1, g2, shift):
    out = []
    for t, p in ours:
        m1 = any(abs(t + shift - tt) <= 0.06 and (p - pp) % 12 == 0
                 for tt, pp in g1)
        m2 = any(abs(t + shift - tt) <= 0.06 and (p - pp) % 12 == 0
                 for tt, pp in g2)
        if m1 != m2:
            out.append(0 if m1 else 1)
    return out


def score(parts: dict[str, list[tuple[float, int]]]) -> None:
    g1, g2 = truth_notes(TRUTH[0]), truth_notes(TRUTH[1])
    both = g1 + g2
    rows = {}
    for name, ours in parts.items():
        shift = best_shift(ours, both)
        a = assign(ours, g1, g2, shift)
        if not a:
            rows[name] = (0, 0, 0)
            continue
        n1 = a.count(0)
        rows[name] = (len(ours), len(a), n1)
    # best part<->guitar mapping (2 parts x 2 guitars)
    names = list(rows)
    consistent = 0
    assigned = sum(r[1] for r in rows.values())
    if len(names) == 2:
        (na, (_, ta, a1)), (nb, (_, tb, b1)) = rows.items()
        map1 = a1 + (tb - b1)          # a->g1, b->g2
        map2 = (ta - a1) + b1          # a->g2, b->g1
        consistent = max(map1, map2)
    for name, (n, matched, n1) in rows.items():
        maj = max(n1, matched - n1)
        print(f"  {name:14s} notes={n} assigned={matched} "
              f"g1={n1} g2={matched - n1} "
              f"purity={maj / max(1, matched):.2f}")
    if assigned:
        print(f"  VOICE ACCURACY {consistent}/{assigned} "
              f"= {consistent / assigned:.2f}")


if __name__ == "__main__":
    from tabforge.core.fretboard import NoteEvent
    from tabforge.core.partition import split_lead_rhythm
    from tabforge.pipeline import _revive_notes

    # fresh split of the CURRENT code: merge the cached run's guitar
    # notes back together and re-split (the cache holds an old split)
    st = json.loads((LOKEN / "parts.json").read_text())
    merged = []
    for name, part in st.items():
        if name.startswith("guitar"):
            merged.extend(_revive_notes(part))
    merged.sort(key=lambda n: n.start)
    res = split_lead_rhythm(merged)
    if res is None:
        print("[loken] split=NONE (wrong — two truth guitars exist)")
    else:
        lead, rhythm = res
        print("[loken 2-guitar truth] fresh split")
        score({"lead": [(n.start, n.pitch) for n in lead],
               "rhythm": [(n.start, n.pitch) for n in rhythm]})

    # decision side: ONE true guitar must not split — the solo-corpus
    # guitar truth MIDI is a genuinely single-guitar performance
    pm = pretty_midi.PrettyMIDI(
        str(ROOT / "Tracks and midi/Only instruments/"
                   "Guitar (Electric Guitar).mid"))
    notes = sorted((NoteEvent(n.pitch, n.start, n.end - n.start)
                    for i in pm.instruments for n in i.notes),
                   key=lambda n: n.start)
    verdict = split_lead_rhythm(notes)
    print(f"[solo guitar truth] split="
          f"{'YES (wrong)' if verdict else 'no (correct)'} n={len(notes)}")
