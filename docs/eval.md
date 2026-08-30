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

## Low-register octave double-pass — 2026-08-26 (task 50)

The theory: Basic Pitch's frequency resolution collapses below
~100 Hz, so a second pass over the audio declared at 2× sample rate
(+12 semitones, zero shifter artifacts; times doubled and pitches
dropped back afterwards) should own everything below A2
(`audio/lowregister.py`, `--low-pass`).

The stand said NO — on a dedicated register fixture (drop-C chugs +
a 5-string bass down to B0) plus the two low pieces:

- bass mean F1 **0.15 → 0.07** with the pass on (synth_pop bass
  collapsed 0.20 → 0.03); F1-below-C3 0.17 → 0.09;
- guitar flat (0.45 → 0.50 overall, 0.44 → 0.42 below C3);
- octave-error rate was ALREADY low without the pass (bass 0.05 on
  the register fixture): on this stand the low-register misery is
  separation mush (metal bass stem yields 8 est notes of 64 either
  way), not the transcriber's resolution.

**Verdict**: default OFF. The machinery ships behind
`PipelineOptions.low_pass` / `--low-pass` for re-testing on real
golden fragments, where separation of a real bass is far better and
the theory may yet hold. The Fulgrim insight also lands here: that
track's "bass problem" was piano misattribution all along — golden
fragments beat synthetic guesses.

## The MT3 experiment — 2026-08-26 (task 51)

YourMT3+ (YPTF.MoE+Multi noPS checkpoint, CPU) wrapped as an
experiment harness in `scripts/mt3_experiment/` — the whole MIX in,
notes WITH instrument labels out, no separation step at all.

- **On the synthetic stand it is blind**: the metal piece returned
  ZERO notes, bass/drums near-zero everywhere. A model with a strong
  instrument prior rejects unfamiliar timbres outright, while
  pitch-blind Basic Pitch transcribes anything — so the synthetic
  stand cannot judge MT3. (Its one glimpse of form: ballad guitar
  F1 0.71, the best number the stand has ever recorded.)
- **On the real Fulgrim it nailed the attribution** that our pipeline
  keeps getting wrong. Against the user's sheet music (a piano piece
  with faint orchestra, no guitar): Acoustic Grand 232 notes + String
  Ensemble 237 (the two real layers), guitar 16, bass 4 — while
  separation+BasicPitch invents hundreds of "guitar" notes there.
- Speed: ~0.7–1× realtime on CPU (48 s for the 65 s track) — cheaper
  than the RoFormer separation alone.

**Verdict**: the branch lives. The hybrid — MT3 decides WHAT plays
(instrument attribution + notes), stems remain for the backing track
and playback — is validated on real material. Full product
integration (a TranscriptionBackend) is the next big task; the
user's incoming real tracks with sheet music are its acceptance
tests.

## THE GOLDEN STAND — 2026-08-26 (`scripts/eval_golden.py`)

The user delivered the real thing: three tracks with per-instrument
MIDI ground truth in "Tracks and midi/" — Hero of Mankind (full band,
6.4 min, ~133 BPM, bass to B0), Loken (full band, TWO guitar parts,
96 BPM, drop-A-territory bass), FulgrimUpd (the piano piece). MIDI
spans match the audio; Suno's impossible key signatures (9 sharps) are
neutralized in the loader.

### Pipeline baseline (demucs, current defaults) — real numbers

```
inst     F1    oct   leak   confirms the user's ears
guitar   0.24  0.16  0.46   Loken P=0.71 but R=0.17: right notes, 83% missed
bass     0.34  0.44  0.59   the LOW-REGISTER octave disease is real here
drums    0.38  0.03  0.10   P up to 0.76, R 0.39: misses hits, rarely wrong
vocals   0.13  0.02  0.23   the semi-recitative barely transcribes
synth    0.03  —     0.42   lands anywhere but home
```

### MT3 on golden: the hybrid narrows

On heavy Suno metal the MT3 experiment LOSES as a transcriber —
walls of distorted guitar are outside its world (Loken guitar: 12 est
notes of 6890; bass 4-14 notes): guitar 0.24-vs-0.06, bass
0.34-vs-0.01, drums 0.38-vs-0.29 in the pipeline's favor; vocals tie
(0.13). Its virtues stay: near-zero leakage and truthful attribution
(Fulgrim piano recognized as piano). Verdict: MT3's role shrinks to an
instrument-presence arbiter (a stronger tagger), not a note source.

### Octave double-pass, retested on real bass: still no

Bass F1 0.34→0.33, oct 0.44→0.38 (Hero 0.64→0.50, Loken worse),
guitar slightly down. Default stays OFF. The octave errors evidently
come from the transcriber picking harmonics in the separated stem, not
from frequency resolution.

### Bass octave errors: three more fixes measured dead (golden, offline)

The errors are REAL (null test: ±12 matches on missed notes are 8-60×
more frequent than ±7/±4 — mechanism, not matching noise), they go in
BOTH directions (Hero 175 up / 191 down; Loken 21 up / 162 down), and
every cheap cure failed on the saved golden estimates:

1. **Sub-harmonic spectral correction** (f0/2 vs f0 vs 2f0 energy in
   the stem): F1 down on Hero, flat on Loken. The stem's spectrum
   AGREES with the "wrong" octave — synth bass patches layer a
   sub-oscillator, both octaves genuinely sound, Suno's MIDI logs one.
2. **Blanket ±12 (notation convention)**: worse; medians already match.
3. **Line-continuity Viterbi** over {-12, 0, +12}: worse or flat —
   metal bass legitimately leaps octaves, and with precision 0.25-0.34
   the est context is too polluted to trust.

The real upstream problem: the bass estimate carries 1.4-1.8× MORE
notes than the truth.

### Threshold sweep and RoFormer on golden: the ceiling is mapped

- **Bass threshold sweep** (onset 0.5-0.7 × frame 0.3-0.4 against the
  cached golden stems): every tightening drops recall faster than
  precision rises — the current preset (0.45/0.25) already sits at the
  F1 optimum (Hero 0.24 raw, Loken 0.40 raw). Octave-error rate falls
  0.45→0.13 with stricter thresholds (the octave matches live in ghost
  notes) but never with an F1 gain. Side-finding: quantization HELPS
  matching on Hero (+0.09 — Suno MIDI is grid-aligned) and hurts on
  Loken (−0.06); timing tolerance interplay, worth remembering.
- **RoFormer separation on golden**: the synthetic-stand win did NOT
  transfer — bass 0.24 vs demucs 0.34, everything else flat or worse
  (Fulgrim piano 0.05 vs 0.14). Demucs stays the default on merit now,
  not just speed.

**The strategic picture after six measured approaches in one day**
(sub-harmonic, blanket shift, continuity DP, double-pass retest,
threshold sweep, separator swap): Basic-Pitch-on-separated-stems is
squeezed dry at F1 ≈ 0.24-0.40 for bass/guitar on this material.
Meaningful gains now require different NOTE SOURCES per stem
(dedicated bass/vocal transcribers, frame-level models), better
truth-side alignment handling, or leaning into the human loop the
editor already provides. Every cheap lever has been measured and
documented — nothing was left to feelings.

Now with real ground truth for one track: the user provided the
actual sheet music for FulgrimUpd.wav — it is a PIANO piece (dense
two-hand writing, faint background orchestra, no guitar, likely no
drums) at quarter = 73–82. Expectations for the analyzer: piano
found, guitar absent, drums quiet; and the detected 152 BPM is a
DOUBLE-TIME error (152 ≈ 2×76) — the tempo family chooser needs an
octave-of-tempo fix. Guitar checks live on Techno_2_sliv (drop-A
rhythm, 7-string suggestion).

2–3 real tracks × 8 flagged bars with hand-written correct MIDI, to
check the stand's conclusions against reality. Needs the user's ears;
tracked in the accuracy plan.

## MEASUREMENT HYGIENE — 2026-08-26 (task 52: the ruler was crooked)

Before betting on specialized transcribers, the ruler itself was
audited. Three defects found, two fixed permanently in
`scripts/eval_golden.py`:

### 52.1 The truth MIDI is time-shifted vs the audio

Onset cross-correlation (10 ms bins, ±500 ms window, pooled over all
instruments) finds a global truth↔audio offset per track: **Loken
−220…−240 ms uniform across every instrument** (a Suno render/export
artifact), Hero −40 ms, Fulgrim −130 ms. Per-instrument checks on
Hero show it is NOT global there (bass −25, guitar −65, vocals
−235 ms) — but per-instrument alignment would overfit noisy
estimates, so the stand aligns globally per track (only when
|shift| > 20 ms). With the old unaligned 80 ms ruler, Loken's entire
scoreboard carried a hidden −3 to −5 points; Loken drums alone jumped
0.24 → 0.58 once aligned.

### 52.2 Strict vs pitch-only: half the "errors" are timing

The stand now reports two columns: **F1** (strict: onset within
50 ms + pitch) and **pF1** (pitch-only: 500 ms window). The gap is
the rhythm/quantization error budget: guitar 0.22 vs 0.35, bass 0.33
vs 0.47, vocals 0.16 vs 0.34, piano 0.26 vs 0.48. Roughly half of
what the old table called transcription failure is timing — fixable
by better beat tracking/quantization, not by better note models.

### 52.3 Octave-split twins: real, but dedup is not a cure

42–45% of est bass notes have a time-overlapping ±12 twin. The truth
side explains why no single dedup works: **Hero's bass part is
WRITTEN in octaves (70% of truth notes have a truth twin), Loken's is
monophonic (1%)**. Upper-wins dedup on Loken: F1 0.306 → 0.321
(P +0.055, R −0.025) — the first positive dedup result; the same
operation on Hero deletes half the written part. Verdict: dedup
cannot be automatic; it becomes a mass-editor operation ("collapse
octave doubles in selection", task 55).

### 52.4 Guitar threshold sweep on the honest ruler

Loken guitar (P 0.68 / R 0.16 — right notes, most missed) responds
massively to softer Basic Pitch thresholds; Hero (dirty stem) does
not:

```
preset                          Hero F1   Loken F1
current  0.5 /0.28/100ms          0.17      0.27
soft     0.4 /0.22/100ms          0.17      0.35
softer   0.35/0.20/ 70ms          0.18      0.40   <- new default
floor    0.25/0.15/ 50ms          0.13      0.44
subfloor 0.2 /0.12/ 40ms          0.10      0.35
```

`softer` shipped as the guitar preset (transcribe.py). End-to-end
(with pipeline quantization) it lands at Loken 0.26→0.36, Hero
0.18→0.16: +0.04 mean, the biggest single gain of the accuracy war,
at the cost of Hero precision (0.17→0.11 — dirty stem, more ghost
notes). Recovering that precision by validating soft guitar notes
against the stem spectrum (the leak-filter machinery in support mode)
is a task-53 candidate. `floor` (0.25/0.15/50) is the clean-stem
optimum (Loken 0.44 raw) — an adaptive threshold could claim it
later.

Stand bug found on the way: `parts.json` is merge-on-write, so parts
from an earlier run with different settings (a lead/rhythm split that
no longer triggers) survived and were double-counted by the scorer —
Hero guitar showed a phantom est flood. `eval_golden.py` now deletes
the state file before each run.

### 52.5 THE RECOMPUTED CEILING (honest ruler: aligned, 50 ms strict)

Definitive end-to-end run — demucs stems, shipped presets (incl. the
new softer guitar), clean state, global alignment, strict tol 50 ms:

```
inst    F1    pF1   oct   vs old   what changed
guitar  0.26  0.41  0.20  0.24     softer preset: Loken 0.36, Hero 0.16
bass    0.33  0.47  0.41  0.34     same ceiling, now honestly measured
drums   0.55  0.59  0.08  0.38     the "0.38" was a ruler artifact
vocals  0.16  0.34  0.03  0.13     Loken 0.19 once aligned
piano   0.26  0.48  0.06  —        new row (Fulgrim now in the cache)
synth   0.04  0.11  0.06  0.03     hopeless without a note source
```

Targets for the specialized-transcriber bets (task 53/54), rechecked:
- **bass**: strict 0.33 / pitch-only 0.47 / octave 0.41 — the mono-f0
  path (RMVPE/torchcrepe/pyin) still attacks the right disease
  (octaves + ghost polyphony). Target unchanged.
- **vocals**: 0.16 / 0.34 — the recitative rule still justified.
- **guitar**: stays Basic Pitch; the honest gap is now TIMING
  (F1 0.26 vs pF1 0.41) — beat/quantization work, plus precision
  recovery on dirty stems.
- **piano**: MT3 scores 0.57 vs our 0.26 on Fulgrim — task 54's
  arbiter is also a piano note-source candidate.
- **drums** at 0.55 drop out of the crisis list.

### MT3 rescored with the honest ruler: role widens

Alignment rescues MT3 too: **Fulgrim piano 0.57 vs our 0.26** (2×),
Loken vocals 0.28 vs our 0.19. Still blind to Suno metal guitar/bass
(4–14 notes of thousands); drums lose to our classifier (0.43 vs
0.55). New verdict for task 54: MT3 is an instrument-presence arbiter
AND a candidate note source for clean piano/vocal stems — not for
guitar/bass/drums.

## SPECIALIZED TRANSCRIBERS — 2026-08-26 (task 53: the mono path)

`InstrumentProfile.transcriber` now selects the note source per stem:
`basic_pitch` (default) or `mono` (`src/tabforge/audio/mono.py`).
The mono path: pyin f0 track (librosa, free; torchcrepe is the
optional `tabforge[crepe]` extra — MIT code AND weights bundled in
the wheel; RMVPE rejected as default: official repo publishes no
weights, the community checkpoint's provenance is an unattributed HF
MIT tag) → onset segmentation → per-syllable decision → one note per
held pitch. Monophony kills octave twins BY CONSTRUCTION.

### Bass: mono wins clean stems, chooser guards dirty ones

```
                 strict F1   pF1    vs Basic Pitch
Loken  (clean)     0.44      0.66    0.31 / 0.48   ← mono, +0.13
Hero   (dirty)     0.11      0.29    0.34 / 0.45   ← BP kept it
```

Two mechanisms made the Loken number possible:

1. **Octave convention, not octave error**: pyin locks the acoustic
   fundamental (A1 = 55 Hz); Suno's truth logs the SAME line at A2.
   The stand now tries a global ±12 per part (like the time shift)
   and reports the chosen k. A per-note spectral octave chooser (odd
   vs even harmonic stacks) was measured and rejected — both octaves
   genuinely sound in the stem (sub-oscillator synth patches), the
   odd-harmonic test fires on <55% of notes at any threshold.
2. **The density chooser**: a stem with heavy bleed defeats pyin (it
   locks onto 401 events where BP hears 1949 — Hero). The pipeline
   runs both and keeps mono only when it caught >= 0.4× of BP's
   note count (Hero 0.21 → BP; Loken 0.65 → mono). Provisional
   threshold, n=2 tracks; revisit with more references.

### Vocals: tie on F1, recitative honesty on top

Mono vocals with the recitative rule: Loken 0.18 vs BP 0.19, Hero
0.11 vs 0.12 — a tie on strict F1, and the mono path additionally
marks 25–50 unpitched-but-energetic syllables per track as DEAD notes
(x in gp5) instead of inventing pitches. Kept as the default: equal
accuracy, honest rhythm marks (the user's explicit ask). Dead notes
are excluded from est in the scorer — rhythm marks, not pitch claims.

Implementation notes: pyin window scales with fmin (4096 for bass's
26 Hz floor, 2048 for vocals — a wider window smooths speech into
false 'held' pitches); the recitative gate demands a 100 ms hold
BECAUSE pyin's own 93 ms window manufactures ~60 ms quasi-plateaus
out of anything; unstable short slivers (pyin gliding between notes)
are dropped, long unstable stretches keep their median (slides/deep
vibrato).

### The ruler, refined again: per-INSTRUMENT alignment

Wiring the mono path exposed two more ruler defects, both fixed:

1. A pooled onset cross-correlation is quasi-periodic on
   grid-quantized material — when the mono path changed the est mix,
   Fulgrim's "global shift" jumped to a spurious −0.49 s (a beat-comb
   peak) and piano strict F1 collapsed 0.27→0.09 with NO change in
   the piano est.
2. There is no single per-track offset to find: Suno exports each
   instrument's MIDI separately, and the offsets genuinely differ per
   FILE (Hero: drums −50 ms, guitar −65 ms, bass −20 ms, synth
   −260 ms). Any global shift trades one instrument against another.

The stand now aligns each instrument by argmax of strict F1 over
(time offset × octave convention) searched JOINTLY — a wrong octave
zeroes F1 at every shift, hiding the true offset (the mono-bass
chicken-and-egg). Instruments with <50 notes on either side fall back
to a weighted-median global (weight = pitch-class match-histogram
peak, immune to both the beat comb and the octave gap). Every system
scored on the stand gets the same single-parameter favor.

### The ceiling after task 53 (per-instrument ruler, end-to-end)

```
inst    F1    pF1   oct   note
bass    0.37  0.56  0.34  Hero 0.34 (BP kept by chooser) / Loken 0.41
                          (mono, octave-error rate 0.16 -> 0.02)
guitar  0.28  0.41  0.21  softer preset: Hero 0.19 / Loken 0.36
drums   0.55  0.59  0.08  unchanged
piano   0.27  0.48  0.05  unchanged (MT3 offers 0.57 — task 54)
vocals  0.17  0.31  0.04  mono+recitative, ties BP + honest crosses
synth   0.05  0.12  0.07  still needs a real note source
```

## THE MT3 ARBITER IN THE PRODUCT — 2026-08-26 (task 54)

`src/tabforge/audio/arbiter.py`: when TABFORGE_MT3_DIR points at a
YourMT3+ install, run_analyze transcribes the whole mix once (~1×
realtime CPU, cached) and every instrument card gets a verdict —
**found / absent / uncertain** — on top of its RMS status. Phantom
cards start unchecked (still clickable); "uncertain" keeps the card
checked. No install → analyze behaves exactly as before.

MT3 silence is ambiguous (blind on metal vs genuinely absent), so the
verdict rests on three signals, each measured on the golden corpus:

1. **Density**: MT3 notes/min per card. ≥20/min (≥60 for drums) =
   found. Fulgrim piano 222/min; Loken guitar 2.5/min.
2. **Content matching against MT3's own mix transcription** (guitar
   and drums). The key insight, found when the user's live Fulgrim
   run defeated two successive tag-based guards: MT3 DID hear the
   phantom guitar stem's melody — and filed it under piano. So the
   guitar guard transcribes a 30 s stem sample and measures the share
   of its notes that MT3 heard as ANOTHER pitched instrument
   (time + pitch-class): phantom 0.36/0.37 on two independent
   separations vs real guitar 0.05 (Loken) / 0.12 (Hero) — threshold
   0.25. Drums mirror it with onsets: a real kit's hits are covered
   by MT3's own drum notes (0.93-0.98) even when MT3 undercounts,
   Fulgrim's phantom (piano attacks) is not (0.28-0.31) — threshold
   0.6. Deterministic (MT3 + BP have no run-to-run randomness), which
   PANNs tags on bleed stems are NOT: the Guitar tag on the SAME
   phantom swung 0.21 → 0.45 between demucs runs (random shifts), and
   the distortion-family variant swung past its threshold too. Tags
   survive only where they are semantic rather than timbral — vocals:
   Singing+Speech+Rapping ≥ 0.25 (Hero 0.69 / Loken 0.53 vs Fulgrim
   0.13). Other dead ends documented: top-2 tags (both stems tag
   "Guitar"), leak-share for guitar (0.56-0.92 EVERYWHERE — guitar
   stems leak by construction, which is why the leak filter exempts
   them).
3. **Leak share** for bass, where PANNs fails outright (synth bass
   scores Bass guitar 0.02 on REAL Hero bass) and content matching
   is unsafe (bass doubles the guitar's roots at the same pitch
   class: Hero foreign-match 0.21 vs phantom 0.26 — too close):
   share of the stem's sampled notes whose harmonics live in another
   stem, taken as the MEDIAN over three 30 s windows at filter
   margin 1.2. Phantom 0.43/0.45 (two separations) vs real 0.03
   (Loken) / 0.07 (Hero), threshold 0.20. The median matters twice:
   a single mid-track window slipped under the old threshold on the
   user's live separation, and Hero's REAL bass has one locally
   dirty section (0.26 at the 2/3 window vs 0.01-0.11 elsewhere)
   that a max-statistic would have flagged. More dead ends
   documented: low-band energy fraction (demucs bass stems are all
   ~0.85 <120 Hz by construction) and RMS share (phantom 0.101 vs
   real 0.113 — inseparable).

**Acceptance (real MT3 MIDIs + real tagger + real leak, 17/17 —
including a SECOND, fresh Fulgrim separation that fooled the original
Guitar-tag guard)**:
Fulgrim = piano found, strings found (other), guitar/bass/drums
absent — matches the user's sheet music exactly; Loken = guitar
uncertain (CHECKED — the blindness guard holds), bass/vocals kept,
drums found; Hero = guitar found, bass kept. The "other" card is
never auto-unchecked — the catch-basin has no self-identity to test.

Not done here (candidate for later): MT3 as a note SOURCE for clean
piano (0.57 vs our 0.26) — the arbiter now caches mt3.mid in the job
dir, so a future "use MT3 notes for this stem" toggle has its data
ready.

## HUMAN IN THE LOOP — 2026-08-26 (task 55)

The loop the whole stand exists for now closes IN the product:
drag-select bars → mass ops (octave shift, delete, reassign to
another instrument, and "collapse octave doubles" — the 52.3 dedup
that is only safe as a human decision); per-note confidence
(velocity blended with harmonic support share) stored in parts.json,
surfaced as a Review mode that overlays and steps through disputed
notes; and "export reference" — the corrected project leaves as
per-instrument MIDI named EXACTLY like the golden corpus
("<track> (Guitar).mid"), so a user's correction drops straight into
"Tracks and midi/" and the eval stand scores every future version
against it. program → human → reference → next version.

## GUITAR TIMING + TEMPO DOUBLING — 2026-08-26 (task 56)

### The snap was the enemy — all of it

Investigation per plan: onset-error histograms (est−truth, same
pitch, aligned) BEFORE vs AFTER quantization on cached golden
estimates. Findings, in order of death:

1. **No systematic detector lag**: raw onset medians are 0..−10 ms on
   both tracks — the "guitar attack smear" hypothesis is dead, no
   constant profile shift needed.
2. **No grid phase error**: onset-vs-grid residual medians are 0-1 ms
   — the phase-compensation idea is dead too.
3. **Partial snap strengths are the worst of all worlds** (Loken:
   raw 0.396, strength 0.9 → 0.348, strength 0.6 → 0.320): a
   half-pulled note lands between raw truth and the grid.
4. **The pre-export quantize is redundant**: the gp5 writer maps
   notes onto grid slots at export anyway (same nearest-tick
   decision). The snap added NOTHING to notation and destroyed
   timing everywhere else — parts.json, playback, the editor, the
   reference export, the eval.

`quantize_strength` default 0.9 → **0.0**. End-to-end on golden:

```
inst    was -> now   pF1   note
piano   0.27   0.44  0.48  gap nearly closed
bass    0.37   0.43  0.56  Loken 0.41 -> 0.61 (P 0.75)
guitar  0.28   0.29  0.41  Loken 0.36 -> 0.40 (goal >=0.35: met there;
                           Hero stays 0.19 — its stem's own pF1 is 0.31,
                           matching noise owns the rest)
drums   0.55   0.55
vocals  0.17   0.15        −0.02, alignment jitter on mono vocals
```

### Double time: the root was a PHANTOM tempo source

Fulgrim's 152 BPM came from beat-tracking the DRUMS stem — which on
drumless material is bleed hiss (RMS passes, rhythm doesn't). The
onset-envelope crest factor separates a real kit from hiss by 10×
(Hero 59 / Loken 43 vs Fulgrim 5.5): `choose_tempo_source` now
requires crest ≥ 15, drumless tracks fall back to the mix.

The mix still prefers double time (161.5), so a second, musical rule:
on keys-led material (piano found by the arbiter, no kit passed the
crest gate) where the piano's strong-attack median IOI runs at the
BEAT rate of the chosen grid — nothing between beats — the tempo is
halved and the beat list thinned. Fulgrim: 152 → **80.7 BPM**, inside
the sheet's 73-82. Hero/Loken take tempo from their real kits and
never consult the rule. Dead ends documented: even/odd beat-envelope
ratio (Hero's snare puts MORE energy on odd beats — 1.30), median
note duration vs half-tick (does not discriminate), pooled-IOI
(chord rolls and BP re-attacks drown it; per-part strong-attack IOI
with the arbiter's verdict is what works).

## NOTE-SOURCE ROUTING — 2026-08-26 (task 57, part 1: MT3 for keys)

`InstrumentProfile.note_source` ("stem" | "mt3"): an instrument can
now take its notes from the arbiter's cached whole-mix MT3
transcription instead of Basic Pitch on its separated stem. Piano is
the first (and per the >=0.05 rule, so far the only) switch:

- MT3 rescored under the current per-instrument ruler: piano 0.57 /
  our stem path 0.44 (+0.13). End-to-end through the pipeline the
  routed piano lands at **0.58** on Fulgrim — the chord gather even
  adds a hair, and no-snap keeps MT3's timing intact.
- Mix-sourced notes are exempt from the leak filter and from
  stem-spectrum confidence: judging them by where demucs happened to
  put the energy would re-import the separation's diseases — which is
  the point: the piano-bleeds-into-other and missing-upper-register
  problems never enter this path at all.
- Only the stem that IS the card routes (an "other" stem treated as
  keys must not duplicate the piano's notes); a missing mt3.mid falls
  back to the stem path silently.
- Vocals stay on the mono path: MT3's vocal wins (Loken 0.28 vs our
  0.19) are entangled with its "other" class — not cleanly routable.

## MUSCRIPTOR — 2026-08-26 (task 57, part 2: the war's endgame)

MuScriptor-small (Kyutai/Mirelo 2026, 103M params, ~393 MB, code MIT,
weights CC BY-NC 4.0 gated on HF) transcribes the whole mix at ~0.4x
realtime on this machine — and unlike MT3 it is NOT blind on heavy
Suno material. Frozen-ruler A/B on the three golden tracks:

```
inst     BP-stack   MT3   MuScriptor   routed to
bass       0.43     0.01    0.62       muscriptor (Loken 0.90: P .89/R .91)
guitar     0.29     0.05    0.41       muscriptor (Loken 0.51 at P 0.90)
piano      0.44     0.57    0.60       mt3 (+0.02 < the 0.05 rule)
drums      0.55     0.43    0.56       our classifier (richer voices too)
vocals     0.15     0.21*   0.01       mono path (MuScriptor blind on
                                       semi-recitative; *MT3's win is
                                       entangled with its 'other' class)
synth      0.04     0.06    0.06       stem (nothing helps yet)
```

End-to-end through the full pipeline (fingering, exports, no-snap)
the routed numbers hold exactly: bass 0.63 / guitar 0.41 / piano
0.58. Integration mirrors MT3: an external venv (~/muscriptor,
TABFORGE_MUSCRIPTOR_DIR override) driven by a subprocess runner —
installing into tabforge's own venv is deliberately not offered,
MuScriptor's pins would downgrade torchaudio under demucs. Weights
are never bundled (non-commercial license); without the install every
routed instrument silently falls back to its stem path. No velocity
in its MIDI (all 100) — dynamics come from the stem paths only.

**The accuracy war's scoreboard, first golden baseline -> now:**
guitar 0.24 -> 0.41, bass 0.34 -> 0.63, piano (broken) -> 0.58,
drums 0.38 -> 0.55, vocals 0.13 -> 0.15 + honest dead-note crosses.

## THE SOLO CORPUS — 2026-08-26 ("only instruments/", MIDI truth by the user)

Eight single-instrument tracks measure transcription WITHOUT the
separation variable. Detection matrix first: the real instrument's
card survives on 7 of 8 (Bass, Vocal, Synth2 perfectly clean — one
card, no phantoms). Failures: the catch-basin "other" card sticks to
solo tracks (Keyboard, Guitar); a phantom bass/guitar "uncertain"
survives on Drums/Synth; and PERCUSSION is the worst case — ethnic
percussion matches neither MT3's kit notion nor the drums own-match
guard, so the real instrument is killed while phantoms live. That
failure is direct fuel for task 62's "Solo track detected" card.

Multi-path scoring against the user's MIDI truth (strict 50 ms,
per-instrument F1-argmax alignment):

```
                       Keyboard  Guitar   Bass   Synth(pad)
muscriptor (mix)          —       0.41    0.68     0.07
mt3 (mix)                0.59     0.02    0.02     0.27
solo: BP/mono on mix     0.49     0.31    0.59*    0.19
demucs stem + BP         0.54     0.33    0.33     0.18
                                          *mono path
```

Verdicts:
- **The routing table survives the solo corpus intact**: keys->mt3
  (0.59), guitar->muscriptor (0.41), bass->muscriptor (0.68, P 0.79;
  the mono path is a strong 0.59 fallback). MT3 stays blind on
  guitar/bass even solo; MuScriptor is blind on synth PADS (0.07).
- **Task 62's headline question answered: demucs eats NOTHING on
  clean material** — the stem path matches or beats raw-mix BP on
  every instrument (+0.02 / 0.00 / −0.01). Solo mode's value is
  time (the demucs stage), card sanity, and honesty — not accuracy.
- **Keyboard excess anatomy**: of MT3's 384 notes, 234 carry octave
  twins (the patch's real doubling) and 125 are fast re-articulations;
  the truth logs 188 single notes. All four dedup/merge variants
  measured WORSE than shipping as-is (0.59 vs 0.47-0.57) — the truth
  matches both octaves partially, so any blind collapse loses more
  hits than it gains precision. Same lesson as the golden bass:
  octave dedup stays a HUMAN mass-editor op.
- Tension noted: solo synth pads score mt3 0.27 vs stem 0.18 (+0.09),
  but golden says +0.02 — below the switch rule. Task 62 may route
  "other"->mt3 in SOLO MODE only (the profile supports split
  defaults).

## SOLO MODE — 2026-08-26 (task 62)

`PipelineOptions` / the "Solo instrument" checkbox (mutually
exclusive with HQ separation): no demucs at all — tempo, key, the
MT3 solo-detect ("solo track detected: guitar", one preselected
card, other heard timbres offered unchecked) and both mix-model
caches all come from the original file; every found card points AT
the mix. Backing and leak spectra are meaningless without stems and
are off.

Acceptance on the solo Guitar track: end-to-end F1 0.41 — exactly
the offline MuScriptor number, nothing lost in the chain; the
transcribe stage took 4 SECONDS (cached mix-model + fingering);
auto lead/rhythm split, chords and sections all alive. The demucs
stage disappears entirely; the analyze cost that remains is the MT3
solo-detect (~1x track length) when installed. Without MT3 solo mode
still works: every card offered, the human picks (they know it's
solo — they checked the box).

The measured groundwork (see THE SOLO CORPUS above): separation eats
nothing on clean material, so solo mode's value is time, card sanity
and the cleaner mix-model routing — exactly as shipped.

## MUSCRIPTOR SIZE + CONDITIONING — 2026-08-28 (task 63)

Frozen-ruler A/B, golden + solo corpus, times on Apple Metal:

```
                 small   small+cond   medium   medium+cond
Loken guitar      0.51      0.50       0.53        —
Loken bass        0.90      0.90       0.90        —
Hero guitar       0.30      0.30       0.33        —
Hero bass         0.35      0.35       0.37        —
Fulgrim piano     0.60      0.51       0.65        —
solo Guitar       0.41      0.42       0.46       0.47
solo Bass         0.68      0.71       0.81       0.80
66s-track time     24s        —         61s        —
```

- **Instrument conditioning is dead**: at best +0.03 (below the 0.05
  rule), and it HURT Fulgrim piano by 0.09 — pinning the instrument
  list steers the decoder wrong more than it helps. Graveyard.
- **medium is real**: solo bass +0.13, solo guitar +0.05, golden
  piano 0.60 -> 0.65 — which also beats MT3's 0.57, so KEYS reroute
  to muscriptor-medium whenever its cache carries the medium variant
  (a .variant marker beside the cached MIDI decides). Guitar/bass
  golden gains (+0.02..0.03) stay under the rule, but the shared
  cache means they ride medium anyway once it is the chosen variant.
- Variant selection: TABFORGE_MUSCRIPTOR_MODEL, else medium
  auto-selected the moment its gated weights exist in the HF cache,
  else small. ~2.5x the time of small, ~0.9x realtime on Metal.

## GAPS / HIGH-RESOLUTION GUITAR — 2026-08-28 (task 64)

Riley et al. (QMUL): ICASSP-2024 domain-adaptation model (guitar-fl,
jazz) and the ISMIR-2024 GAPS model (guitar-gaps, 14h classical).
License protocol passed cleanly for once: code MIT, weights MIT on
HF (xavriley/midi-transcription-models) — fully integrable. Runs at
~0.2x realtime on Metal via the Kong piano-CRNN architecture.

Measured against our truth (solo corpus + golden guitar stems):

```
                gaps    fl    muscriptor-med   BP
solo Guitar     0.33   0.37       0.46        0.31
Loken stem      0.25   0.33       0.53        0.27
Hero stem       0.21   0.18       0.33        0.19
```

**Verdict: measured, lost, not integrated.** The domain mismatch the
plan predicted is exactly what the numbers show — precision is
honest (0.80 on Loken: what it hears, it hears right) but recall
0.15-0.24 on distorted Suno material buries it; MuScriptor-medium
wins every row by more than the routing rule's margin. Two
integration nuisances noted for the record: its from_pretrained is
broken against current huggingface-hub (constructor + manual
hf_hub_download works), and its resampy pin (>=0.4.3) conflicts with
basic-pitch's (<0.4.3) — 0.4.2 runs both fine in practice. The venv
install stays: on GuitarSet (task 65, acoustic — ITS home domain)
GAPS becomes the literature-grade comparator for our pipeline.

## THE GUITARSET RULER — 2026-08-28 (task 65): Viterbi measured at last

`scripts/eval_guitarset.py` (GuitarSet, Zenodo 3371780: 360 excerpts,
hexaphonic per-string truth). The fingering engine had NEVER been
measured against how guitarists actually play — now it is:

```
STRING ASSIGNMENT (truth notes -> assign_tab vs the human's string):
overall           0.598   (62,473 notes)
open strings      0.994
fretted notes     0.569
by style          Rock .650  SingerSongwriter .670  Funk .627
                  Bossa .465  Jazz .490
top confusions    G->B 6662   D->G 5717   B->e 5316   A->D 2951
                  (every one of them = we pick the THINNER string)
```

The error is systematic and one-directional: the Viterbi gravitates
to LOW FRETS near the nut, while human hands play in POSITIONS
(frets 5-9, hand stays put) — hence position-heavy styles (bossa,
jazz) score worst and open-position styles best. This is the user's
"unplayable fingering" complaint turned into a number and an
address: the fret-height penalty outweighs hand-movement cost in
static_cost/transition_cost. Per the plan, NOT blind-fixed here —
the histogram is the spec for a dedicated cost-tuning task, and the
0.598 baseline is the ruler it will be measured against.
(Literature reference: audio2guitar claims 97.8% string accuracy.)

### GuitarSet transcription (task 65, part 2): GAPS resurrects at home

Mono-mic audio, 30-excerpt subset (player 00, all five styles),
strict 50 ms mir_eval:

```
GAPS (classical ckpt)   mean F1 0.858   median 0.867
MuScriptor-medium            0.745          0.814
Basic Pitch                  0.590          0.586
```

Yesterday's burial gets an honest amendment: GAPS loses on OUR Suno
distortion but WINS acoustic solo guitar by +0.11 over the incumbent
— beyond the routing rule's margin. Task 66 gets a domain route:
solo acoustic guitar -> GAPS, distorted/mixes -> MuScriptor, with
the PANNs distortion-family score (already computed for the arbiter
guards) as the discriminator. The subset run is the stand's
regression form (`--limit 30`); the full 360 stays on demand.

## TASK 66 ASSEMBLY — 2026-08-28: the guitar-engine table and the routes

The block's closing table — every guitar backend on every stand we
own (strict 50 ms F1; golden = stems from mixes, solo = the no-demucs
path, GuitarSet = acoustic solo, 30-excerpt regression subset):

```
                      BP     MuScriptor   MuScriptor    GAPS
                             small        medium
solo Guitar          0.31       0.41         0.46       0.33
solo Bass            0.53       0.68         0.81        —
solo Keyboard        0.29       0.55         0.60        —
Loken guitar stem    0.27       0.46         0.53       0.25
Hero guitar stem     0.19       0.28         0.33       0.21
GuitarSet (30)       0.59        —           0.745      0.858
```

Routing that survives the >=0.05 rule, written into the product:

- **Mixes** (normal mode): guitar/bass/keys ride MuScriptor-medium
  when its cache exists (task 63), MT3 keeps keys otherwise; BP is
  the no-install floor.
- **Solo mode**: same, EXCEPT the auto guitar engine asks the PANNs
  distortion-family sum (Electric guitar + Distortion + Heavy metal)
  first: < 0.30 means acoustic-flavored -> GAPS (its home domain,
  +0.11 over the incumbent on GuitarSet); >= 0.30 or no tagger ->
  incumbent. Measured stems sit far from the line (Loken 0.57, Hero
  0.49, solo Guitar ~0.5 vs GuitarSet-style material 0.11-0.21).
- **The human outranks the router**: a "guitar engine" dropdown on
  the instruments screen (auto | MuScriptor | GAPS | Basic Pitch);
  the explicit pick bypasses every gate, with graceful fallback
  (missing install/cache -> next in the gaps -> muscriptor -> stem
  chain).

### The block's graveyard (updated)

- MuScriptor conditioning (task 63): max +0.03, piano −0.09 — dead.
- GAPS on distorted/Suno material (task 64): loses every row — the
  domain gate exists precisely to keep it off this material.
- GAPS's guitar-fl (jazz) checkpoint: never beat gaps or the
  incumbent anywhere — not shipped.
- MT3 for solo-guitar notes: 0.41 vs medium's 0.46 — arbiter only.
- Percussion solo-detect (task 62): ethnic percussion matches
  neither MT3's kit notion nor the own-match guard — documented
  failure, card shows unchecked.

## TASK 67 — 2026-08-30: the Viterbi costs meet the humans

`scripts/tune_viterbi.py`: coordinate descent over the six cost
coefficients against GuitarSet string-assignment truth. Protocol per
the plan: split BY PLAYER (00-03 train / 04-05 test — style leaks
across a player's takes), truth notes in (the LAYOUT is tuned, not
transcription), one look at the test set at the end.

```
                         old        tuned
high_fret_penalty        0.05       0.01
move_penalty             0.55       1.6
open_string_bonus        0.35       0.0
stretch_penalty          1.2        0.45
string_change_penalty    0.10       0.0
reach                    3          3

train (00-03)            0.627  ->  0.722
test  (04-05, one look)  0.531  ->  0.647   (+0.116 held-out)
full corpus              0.598  ->  0.699   (fretted .569 -> .687)
by style                 bossa .465->.639  jazz .490->.656
                         rock .650->.743   SS .670->.728
```

The tuning says one thing, loudly: the old weights modeled a guitarist
who hugs the nut and grabs open strings; real hands PLANT in a
position and work the fingers. Hand movement got 3x more expensive,
everything else nearly free. Position-heavy styles gained the most —
the exact mechanism task 65 diagnosed.

The after-histogram is the important part: the one-directional
thinner-string bias is GONE (G->B 6662 -> 3933, and reverse
confusions B->G/e->B appeared at ~1.2k). What remains is two-sided
ambiguity — several boxes fit the same phrase and the human picked
one for reasons our pairwise beam cannot see. That is the plan's
item 6: PHRASE CONTEXT (a human chooses the position for the whole
phrase ahead) — the named lever for the next attack on this ruler.
The 0.75 acceptance line was not reached (0.647 on the hardest
players); the honest reading is that weights alone buy +0.10-0.12
and the rest is a modeling problem, not a tuning problem.

Textbook check (item 4): all 253 unit tests pass with the tuned
defaults — Em stays 022000, the C3 scale stays low (the register
forces it), the solo box holds. The one textbook casualty found by
hand: a C4-C5 scale now lays in 5th position instead of open first —
which is how a position player actually fingers it; the textbook
first-position reading survives only where the register demands it.
Open-string agreement pays for the win (.990 -> .786 on test): the
bonus was swept at 0.03/0.07 and lost both times on train.

## FAST-ANALYZE VIA WINDOWED MT3 — 2026-08-30: measured, failed, buried

The plan (perf.md, task 68): with MuScriptor installed MT3 is only
the presence arbiter, so replace its full pass (40-73% of the wall)
with sampled windows. Three schemes tried against the gate "arbiter
verdicts unchanged on golden", each teaching something:

1. Fixed thirds (start/center/end, per the late-entry refinement) +
   pooled density: Hero guitar AND piano flipped. Pooling was one
   bug — presence is an OR over the song, not an average; per-window
   MAX density fixed piano.
2. Fixed windows, max density: Hero guitar still lost — the full
   pass hears 663 guitar notes, the covered windows contained 0. Not
   sampling luck: a CLEAN 34 s excerpt of a guitar-rich section also
   yields zero guitar.
3. Energy-guided windows (loudest 30 s per third; RMS — onset
   strength is log-compressed and level-blind) + the plain center:
   guitar recovered (the windows found the choruses), but piano
   flipped to absent even though a window sat EXACTLY on the
   full-pass piano cluster (98-128 s, 58 notes there) — the windowed
   clip filed the same audio as other/guitar.

**The real finding: YourMT3+'s instrument attribution rides on LONG
context.** In 30 s excerpts the model re-files distorted guitar as
"other" and band-buried piano as other/guitar — the full pass carries
instrument identity across the song in a way excerpts cannot. The
verdicts differ not because the sample is too small but because the
MODEL answers a different question on short clips. Savings had also
shrunk while chasing the gate (Hero 398 s -> 153 s with 4 windows,
Techno 162 -> 95 s) — 2.6x at best, on top of a failing gate.

Buried; the escape data stays here. If fast-analyze returns, the
lever is a lighter PRESENCE model (PANNs-style tagging over windows —
attribution-free by design), not a windowed transcriber. MT3's full
pass remains the price of its arbiter.

## TASK 70 — 2026-08-30: phrase context — two hypotheses die, a prior wins

The task-67 residue (test 0.647, two-sided box ambiguity) attacked in
three measured steps on the same ruler (train 00-03, ONE test look):

1. **Beam width is innocent**: 80 -> 200 -> 400 changes nothing
   (train frozen at 0.7220). The DP is not truncation-limited — the
   cost model is the ceiling. 30 seconds well spent.
2. **"When to move" is not the problem**: free hand relocation across
   rests (move_free_gap) and a steeper in-phrase move tax
   (time_factor_k alone) both swept to OFF/no-change. Graveyard.
3. **Error anatomy pointed at the real term**: 75% of errors sit
   LOWER on the neck (median 5 frets), P(err | prev err) = 0.77 with
   66% of errors in streaks >= 4 — whole phrases in the wrong box —
   and solo passages err 2x worse than comping (0.42 vs 0.22: chords
   anchor the position, melodies float). The missing preference: a
   V-shaped POSITION PRIOR pulling toward mid-neck.

With the prior (center 5, weight 0.05) the descent re-balanced the
whole system — the hand got costlier still (move 1.6 -> 3.4), the
stretch tax came back (0.45 -> 1.2), the in-phrase decay steepened
(k 3 -> 5):

```
                      task 67     task 70
train (00-03)          0.722       0.779
test  (04-05)          0.647       0.743   (from 0.531 pre-tuning)
full corpus            0.699       0.768
by style               rock .743->.812  SS .728->.773  funk .762
                       bossa .733  jazz .733
```

Held-out gain +0.096; cumulative since the hand-set weights +0.21.
The 0.75 acceptance line from task 67's spec is a hair away (0.743).
Textbook fallout, decided consciously per the spec (live players
win): the C3 scale test now asserts ONE coherent box instead of
"first position", and the pin-mechanics test runs with the prior off
(it tests pins, not layout). Opens pay again (corpus .870 -> .744) —
the prior trades open-string agreement for phrase-box agreement, and
the humans' own data priced that trade.

## THE STRUMMING REGRESSION — 2026-08-30: 32nd walls on acoustic chords

The user's acoustic track (solo mode) rendered strummed chord bars as
walls of 32nds again — v0.7.4's calibration had missed the mechanism.
Synthetic reproduction nailed it: a STRUM spreads a chord's onsets
past the 45 ms event-grouping window, the split shapes get pushed
into separate 32nd cells by the collision rule, and the picker sees
"real" fine structure (8th strums + 2 tail notes: 149/200 measures
escalated to d8). Suno mixes barely strum (2-65 tail pairs per
track — why Techno never showed it); GAPS on acoustic strumming
writes every string separately.

Fix at the ROOT, not in the picker: the guitar profile now gathers
chords (chord_gather_window 0.08 — the same anchored
"joins-only-if-the-first-note-still-sounds" gather the piano has had
since task 56). Fast runs never gather (their notes do not ring
together); measured end to end: 8th strums 149/200-in-d8 -> 200/200
in d2, 16th strums -> d4, true 32nd runs still escalate.

Prices, both accepted: GuitarSet strings test 0.743 -> 0.736
(−0.007, noise), golden guitar F1 0.41 -> 0.40 (−0.01, the moved
tail onsets vs the 50 ms tolerance) — everything else identical.

## THE TEMPO OCTAVE — 2026-08-30: the real culprit behind "32nd walls"

The user's acoustic track ("Просто так вышло") kept rendering in
32nds after TWO adaptive-grid fixes — because the grid was innocent
this time. The score said 81 BPM; the song is 161.5. At 81 every
eighth is notated a sixteenth and every sixteenth a thirty-second:
the notation was CORRECT for a wrong clock.

Mechanism (dissected on the track): the detector's hypothesis set
derives 3/4 and 4/3 spins of the top families, and 107.7 (itself the
2/3 alias of 161.5) spun off 80.75 — which INHERITED the top
family's weight (6292 votes) despite its own family carrying just
335, and whose sparse grid hits only the strongest strums (envelope
5.39, the scorer's documented slow bias). Score 33886 vs the true
161.5's 19787.

A truth stand existed all along: Suno MIDIs carry tempo meta. Eight
tracks with known tempo (golden three + solo corpus four + this one),
current detector: **5/8**, octave errors in BOTH directions (Fulgrim
and solo Keyboard ×2 — the task-56 keys rule papered over only the
first, and only in mix mode; Prosto ×0.5).

Scorer surgery was tried and REJECTED: replacing inherited weights
with own-family weights breaks the 4:3 cases the inheritance was
built for (Bass/Guitar/Synth flip to 127.6), and envelope salience
cannot separate 107.7 from 161.5 (both grids land on onsets). The
detector stays untouched.

The fix that benched **9/9**: an octave-correction rule AFTER the
detector, from note evidence (pipeline._octave_correct):

- median material IOI >= 0.9 beat (nothing between beats) -> HALF
  time, floor 55 BPM. Generalizes the keys rule: fixes Fulgrim
  (161.5 -> 80.8, sheet says 73-82) and solo Keyboard alike.
- median IOI <= 0.35 beat AND >= 10% of the raw periodicity votes
  sit within 5% of the doubled tempo -> DOUBLE time, ceiling 185.
  The vote share is the load-bearing guard: the halved-tempo victim
  carried 36% at 2x, true-96 sixteenth tracks (Loken/Bass/Guitar)
  carry 0-2%. Drum-tracked tempi are exempt (the kit already chose).

Acceptance on the victim: 81 -> 161 BPM; the lead's spectrum flipped
from {154 eighths, 603 sixteenths, 99 thirty-seconds} to
{606 eighths, 139 sixteenths, 6 thirty-seconds} — the user's exact
reading ("должны быть 8-е, может где-то 16-е").
