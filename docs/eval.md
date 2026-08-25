# Transcription quality — the measuring stand

`scripts/eval_transcription.py` builds five ground-truth pieces (note
lists rendered with deterministic synthesis: Karplus-Strong strings,
attack-ramped saw brass, detuned-saw synths, the drum-kit generators
from the test suite), mixes them, runs the FULL pipeline — separation
included — on the mix, and scores the result with mir_eval:

- **P / R / F1** — per-instrument note precision/recall (onset ±80 ms +
  pitch), measured against the instrument's HOME part (guitar →
  guitar/lead/rhythm, bass → bass, brass & synth → other/keys,
  drums → drums);
- **oct** — octave-error rate: of the missed reference notes, the share
  that WAS transcribed but an octave off;
- **leak** — the share of an instrument's reference notes that surface
  in parts where they do not belong ("brass became guitar").

Run: `TABFORGE_NO_TAGGING=1 .venv/bin/python scripts/eval_transcription.py`

Honest caveats: the synthetic timbres are *harder* for demucs than real
recordings (it was trained on real instruments), so absolute numbers
are pessimistic — the stand exists to measure **relative** progress.
Demucs inference also uses random shifts, so tiny run-to-run deltas
(±0.05 F1) are noise.

## Baseline — 2026-08-26 (htdemucs_6s + Basic Pitch, pre-accuracy-war)

```
track          inst     ref  est     P     R    F1   oct  leak
--------------------------------------------------------------
brass_ballad   guitar    64   85  0.26  0.34  0.30  0.12  0.92
brass_ballad   bass       8   39  0.08  0.38  0.13  0.20  1.00
brass_ballad   brass     14  229  0.06  0.93  0.11  0.00  0.93
brass_ballad   drums     32   70  0.00  0.00  0.00  0.00  0.09
funk           guitar    48   53  0.00  0.00  0.00  0.00  0.56
funk           bass      48   45  0.27  0.25  0.26  0.03  0.69
funk           drums     96   84  0.57  0.50  0.53  0.00  0.18
metal_drop_c   guitar   168   67  0.60  0.24  0.34  0.12  0.75
metal_drop_c   bass      64    9  0.00  0.00  0.00  0.00  0.98
metal_drop_c   drums     88   63  1.00  0.72  0.83  0.00  0.48
rock_drop_d    guitar    24   32  0.00  0.00  0.00  0.25  0.96
rock_drop_d    bass      64   29  0.45  0.20  0.28  0.10  0.94
rock_drop_d    drums     97   63  0.25  0.16  0.20  0.00  0.09
synth_pop      synth     16  132  0.12  1.00  0.22  0.00  0.62
synth_pop      bass      32   49  0.33  0.50  0.40  0.69  1.00
synth_pop      drums     64   63  0.21  0.20  0.20  0.00  0.00

MEAN           bass                           0.21  0.20  0.92
MEAN           brass                          0.11  0.00  0.93
MEAN           drums                          0.35  0.00  0.17
MEAN           guitar                         0.16  0.12  0.80
MEAN           synth                          0.22  0.00  0.62
```

What the numbers say, in words:

- **Leakage is the number-one disease** (0.6–0.9 for every melodic
  instrument): the notes mostly ARE transcribed — in the wrong part.
  Brass recall is 0.93 with precision 0.06: everything lands, almost
  nothing lands home. This is the separation/attribution war
  (tasks 48, 49, 51).
- **Bass octave errors** hit 0.69 on synth_pop — the low-register pit
  (task 50).
- **Drums** swing wildly (0.00–0.83) — onset extraction is fine when
  the separated stem is clean, and dies when it is not.

Every change from here — thresholds, filters, separators, models —
either moves these numbers or gets reverted.

## Separation A/B — 2026-08-26 (task 48)

Same five pieces, only the separator changes
(`--separator demucs|roformer`). Mean per instrument:

| instrument | F1 demucs | F1 roformer | leak demucs | leak roformer |
|---|---|---|---|---|
| guitar | 0.16 | **0.26** | 0.80 | 0.76 |
| bass   | 0.21 | **0.37** | 0.92 | **0.69** |
| drums  | 0.35 | **0.46** | 0.17 | 0.15 |
| synth  | 0.22 | **0.27** | 0.62 | **0.38** |
| brass  | 0.11 | 0.10 | 0.93 | 0.86 |

BS-Roformer-SW wins F1 on every instrument but brass (tie) — +23…+76%
relative — and cuts leakage across the board. **Cost**: ~3.5× realtime
on CPU vs ~0.1× for demucs (a 3-minute track ≈ 10 minutes to
separate), and a 700 MB checkpoint on first use.

**Verdict**: demucs stays the interactive default; roformer ships as
the "High-quality separation" checkbox on the start screen
(`separator` form field / `--separator` / `TABFORGE_SEPARATOR`).
Requires `pip install 'tabforge[roformer]'`.

## Harmonic leak validation — 2026-08-26 (task 49)

A note claimed by stem X must hold its harmonic energy IN stem X; when
a rival pitched stem carries > margin × that energy at the note's time,
the note is someone else's echo and gets dropped
(`audio/validate.py`, `--leak-margin`, 0 = off).

Measured verdicts (demucs, margin 2):

- **Symmetric filtering is a trap**: applied to every stem it lifted
  synth 0.22→0.35 and brass 0.11→0.14 — and slaughtered guitar
   0.16→0.07 and bass 0.21→0.06, because demucs separates those stems
  so weakly that their REAL notes' energy sits in "other". Margin 8
  did not save them (10-50× dominance).
- **Shipped shape — asymmetric**: validate only the catch-basin stems
  (other / vocals / piano) where everyone's bleed collects; guitar and
  bass are exempt by construction. Result: guitar/bass/drums stay at
  baseline (0.15/0.21/0.35), synth's gain holds (0.34, consistent in
  all three filter runs).
- **Stand noise, honestly**: single-run demucs variance on these short
  pieces is larger than first claimed — synth leak swung 0.44–0.81
  between runs, and the brass fixture (14 notes) is too small to judge
  the filter either way. Bigger fixtures / averaged runs are future
  stand work.

## Golden fragments (real material) — TODO

2–3 real tracks × 8 flagged bars with hand-written correct MIDI, to
check the stand's conclusions against reality. Needs the user's ears;
tracked in the accuracy plan.
