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

Part 2 (MuScriptor A/B) blocked on gated weights — awaiting the
user's HF license acceptance; harness ready in a scratch venv.
