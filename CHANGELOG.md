# Changelog

## v0.7.30 — 2026-09-01

The rest of calibration session 1, all four cases closed. The
two-guitar SPLIT learned the user's 22 marks: 0.08 -> 0.79 on the
flag ruler via top-note peeling (a solo note striking inside a power
chord's window is not part of the chord), a continuous register ramp
and register-aware smoothing (voices smooth within themselves) — at
a measured -0.03 on Loken. BEND RESCUE shipped: flat notes read
their bend/vibrato/slide contours off the stem, and sustained solo
voices the transcription dropped entirely come back as notes with
their bends. The bar-39 DISSONANCE was diagnosed to the exact notes
(a semitone-off cluster confirmed spectrally); the automatic repair
was measured and buried (double-tracked detune defeats it), and the
mass editor gained the missing tool instead: semitone +1/-1 on any
selection. Flags travel with projects; every number is in eval.md.


## v0.7.29 — 2026-09-01

Calibration case #2 (the "no legato between the 32nds" flags): a
16th run played slightly behind the grid made each bar's last note
straddle the barline by 1/24 of a beat — that rounding residue
became a phantom cross-bar tie which occupied the next bar's first
slot and displaced the whole run one slot late (the same spot the
"first chord should be a pickup" impression came from). A
continuation tail shorter than a 32nd is now dropped at the split:
it cannot render as anything but noise, while real held notes keep
their ties. On the flagged track the lead's phantom ties are gone,
bars open on the beat, and only three genuine ties remain.


## v0.7.28 — 2026-09-01

Hotfix from the calibration session: analyze crashed with "not
enough values to unpack" on tracks where the beat-grid ensemble
DECISIVELY SWITCHES to the alternative grid — the rarest branch of
the meter-changes refactor (task 74) still returned two values where
every other path returned three, and no corpus track exercised it in
the acceptance (only hard tracks switch). Fixed and pinned with a
forced-switch replay that also covers the legacy grid-cache format.


## v0.7.27 — 2026-09-01

Calibration flag #1, fixed the same day: with two detected guitars,
one rang bright and loud while the other sat muffled and quiet. A
three-reader audit traced it to a single deliberate line from the
lead/rhythm split's birth — the lead voice was patched as a clean
electric (program 27) while rhythm stayed steel acoustic (25), and
in a real SoundFont those presets differ in both loudness and
timbre. Both guitar voices now share one patch — equal sound, the
score tells them apart, not the mixer (verified in the gp5: same
program, volume and balance on both tracks). The audit also caught
the editor's click-to-hear piano sitting ~5 dB under the guitar
pluck — level-matched.


## v0.7.26 — 2026-09-01

Playback you can trust your ears to (task 80) — after two honest
burials in a real WebKit drive: alphaTab 1.4's synth silently skips
Vorbis samples (MuseScore_General.sf3 "loaded" and played silence —
the bug the first build of this feature shipped) AND stereo samples
(FluidR3: 1310 skips), so the winner is GeneralUser GS — designed
mono, 30 MB, redistribution-friendly, zero skipped samples, verified
end-to-end in WebKit with the real UI. It lazy-downloads on first
use like the model weights, is cached and served locally, and the
player falls back to the CDN bank until it lands or when offline.

## v0.7.25 — 2026-09-01

The calibration flag (task 77's tool): a 🚩 button in the player —
hit it the moment something reads wrong, type five words ("тут
триоль, а не 16-е"), and the flag lands in the project with the bar,
tick, active part and timestamp. Flags travel inside the .tabforge
archive; a calibration session is now listen -> flag -> keep
listening, no screenshots needed (screenshots still work too). Each
flag becomes a numbered case on the next pass.


## v0.7.24 — 2026-09-01

The speed program (tasks 75-76). The fast-arbiter hypothesis —
time-stretched or resampled MT3 input for presence-only listening —
was measured on 28 verdict checks and buried at every rate: mixed
piano collapses under any acceleration (a 6-7x note loss), though
the bench left two diagnostic gems (bass presence IMPROVES an octave
up; slower playback helps low-frequency recall). What did land:
MT3 and MuScriptor now run in PARALLEL (measured on Hero: 571 s
serial -> 395 s wall — Metal interleaves the two for free, MuScriptor
hides entirely in MT3's shadow), whisper lyrics run alongside the
per-part transcription loop instead of after it, and the analyze
screen fills INSTRUMENT CARDS PROGRESSIVELY — each card appears the
moment its facts exist, minutes before the arbiter finishes
listening. MuScriptor under time-stretch was also measured for
transcription and buried as predicted (-28% notes).


## v0.7.23 — 2026-08-31

The tail of the techniques-and-deferred queue. TRILLS: a run of two
pitches alternating FASTER than the song's own metric grid (faster
than sextuplets — a 16th gallop must never fold) collapses into one
note with the trill marking instead of a wall of 32nds. SOLO
PERCUSSION: a drum kit track was already auto-detected; non-kit hand
percussion (congas, shakers) used to fall through to "pick the
instrument yourself" — dense percussive onsets with no pitched
content now preselect the drums card (the default onset detector
barely hears hand percussion: 0.4/s vs 4.5/s through the percussive
component — measured). SECOND-GUITAR note attribution prototyped and
honestly parked: the learned-timbre-profile scheme separates the
entering electric at 90-95% but the acoustic's own notes drift in
the overlap (mixture pollutes their attack windows) — balanced 0.67,
below the note-only splitter; a separation model is the road, the
detection half (timbre timeline) stays ready. The unclaimed +0.04
Basic-Pitch floor preset is consciously left unclaimed: BP guitar
only serves installs without MuScriptor, and touching the floor
invalidates every cached estimate for a win nobody routed sees.


## v0.7.22 — 2026-08-31

Flageolets (natural harmonics) join palm mute in the score — the
same one-pass feature detector, opposite quadrant: a palm mute is
dull AND falls fast, a harmonic is nearly a pure tone that RINGS.
Purity alone turned out to be a continuum on synth-guitar material
(measured — a dark pad rings pure everywhere), so the musical
constraint carries the split: a natural harmonic must also sound
well ABOVE the part's texture, the way 12th/7th/5th-fret chimes ring
over the open string. Conservative rates on real mixes (0.6-1.4%);
notated as the harmonic diamond in the player and Guitar Pro,
survives edits and reloads.


## v0.7.21 — 2026-08-31

Palm mute is in the score: a guitar attack that decays fast AND is
spectrally dull (both windows adapted to the gap before the next
attack) gets the P.M. marking — but only in runs of three or more
(chugging is a texture, one dull note is not), calibrated on
Karplus-Strong synthesis where the damping is ground truth. Honest
limit, measured and documented: on heavily distorted mix-separated
stems the decay cue is physically masked (compression + density), so
on metal the detector stays nearly silent instead of guessing —
clean and acoustic material is where it speaks. Palm mute survives
edits and project reloads (parts.json), renders in the player and
Guitar Pro.


## v0.7.20 — 2026-08-31

Meter changes within a track (task 74, the block-70 finale). One
madmom DBN decode commits to a single bar length almost globally —
measured: 37 seconds of waltz spliced onto a 4/4 track stayed 4/4
until the last bar — so the meter is now ALSO voted per 20-second
window over the same activation, stable runs of votes become meter
segments, and the score writer carves bars by a per-measure meter
list: the time signature changes mid-song exactly where the music
does (snapped to a barline; the detector's boundary is ±2.5 s). The
whole writer moved to variable-measure arithmetic with the uniform
song as a special case — byte-identical output on every existing
test. Conservative by design: a change needs ~35 s of stable new
meter, so straight tracks and the waltz report none (verified). The
window votes also replaced the old max-position meter vote with a
time-share vote — a single noisy bar can no longer flip a track's
signature.


## v0.7.19 — 2026-08-31

The block-70 tails. Review mode works again: note confidence had
silently degenerated to a constant on model-routed parts (no
velocity in MuScriptor MIDI, no spectral support for mix-sourced
notes) so nothing was ever "disputed" — routed notes now earn real
stem-support confidence and Review walks the worst 15% of each part
instead of a fixed threshold. Song sections stop splintering: runs
of unrecognized segments merge (Hero went from 16 sections — eleven
of them "Bridge" — to nine with real structure). A license audit
landed (docs/licenses.md + scripts/license_audit.sh): nothing
non-commercial ships in the bundle, the yellow zone is inventoried,
open verification items are named. Synth pads are documented as a
transcription dead end in Honest limitations (use the Suno MIDI
drop for pad lines). The skip-the-arbiter checkbox turned out to be
shipped already — verified end-to-end and left as is.


## v0.7.18 — 2026-08-31

Durations became an optimization with positions first (task 73).
Measured on real runs before changing anything: positions and
durations were already independent, and most ties turned out to be
correct barline notation — the real clutter was the new mixed-bar
renderer splitting every duration at every beat line. It now splits
only where the grid family actually changes, which on identical
inputs cut tied beats by a fifth to a quarter and tuplet beats by up
to half while leaving every position bit-identical. The song's base
grid (8ths vs 16ths) is now elected by an explicit cost balance —
each bar's displacement+rent against a price for flipping grids
between neighboring bars — replacing the note-mass threshold; it
reproduces every previously blessed choice on fixtures and both real
tracks. New permanent metrics script (scripts/eval_notation.py):
position error and notation complexity of any finished run.


## v0.7.17 — 2026-08-31

Swing and triplets became a property of the beat (task 72). A
shuffled track now reads the way a human writes it: straight 8ths
with a triplet-feel marking in the score — the feel is detected on
the whole song's attack histogram (off-beats at ~2/3 of the beat,
with the guard that real triplet music fills the first third too and
keeps its tuplets). The straight-or-triple decision moved from the
measure to the SINGLE BEAT with a Viterbi price for switching: one
real triplet inside a bar of 16ths renders as a tuplet beat instead
of being crushed into 16ths with a rest hole, and jittered straight
playing cannot flip a bar into junk triplets. When virtually every
beat divides in three the song is written in compound time — 12/8
(or 9/8) with plain 8ths, not walls of tuplets. Verified on synthetic
fixtures (clean and jittered) and on two real mixes: no false swing,
no random triplets, and Prosto's genuine triplet figures land on
exact triplet-grid slots. Six new tests.

## v0.7.16 — 2026-08-31

The packaged app can hear meter now. madmom's downbeat models are
CC BY-NC-SA and stay out of the bundle — instead it lives in its own
venv at ~/madmom/venv (installed on this machine), and the app calls
it as a subprocess, the same license-clean pattern as MT3 and
MuScriptor. Dev runs still use the in-process import; without any
madmom install the app quietly stays 4/4 as before. Non-wav inputs
are decoded to a temp wav first (madmom has no ffmpeg), temp files
are cleaned up, TABFORGE_MADMOM_PYTHON overrides the probe.

Caught live on the first packaged run (the waltz rendered 4/4): the
app's decoded wav is 48 kHz stereo, and madmom's loader resamples
only via ffmpeg — absent in its venv — so the runner died and the
code fell back to 4/4 in silence. The runner now decodes, downmixes
and resamples with scipy itself, and every madmom failure branch
writes out/madmom_error.txt plus a progress warning instead of
swallowing the trace (that silence cost the whole diagnosis).
Verified inside the rebuilt app end-to-end: the analyze log says
"madmom votes 3/4" and the waltz's gp5 carries a 3/4 time signature.

## v0.7.15 — 2026-08-31

Time signatures are real now: a waltz renders in 3/4. The meter is
detected by madmom's bar-length vote constrained to our tempo (8/8
on the stand — 3 on the user's new waltz track, 4 on every straight
track), carried through analyze into the score's time signature, and
the bar-phase election runs modulo the detected meter. Without the
optional madmom install nothing changes (4/4 stays). The waltz's
MIDI stubs proved unusable as timing truth (14 arbitrary notes), so
the stand scores that track meter-only — honestly noted.

## v0.7.14 — 2026-08-31

The beat-grid ensemble: on hard tracks (measured: metal) the built-in
tracker's grid can sit a whole level off, and madmom constrained to
our tempo nails it — so analyze now compares the two grids by which
one better explains the transcribed notes, switching only on a
decisive margin (one deserved switch on the whole stand: Loken's
beat F1 0.27 -> 0.83; mean 0.61 -> 0.69). madmom is optional, never
bundled (CC BY-NC-SA models — the same yellow zone as MuScriptor):
without it nothing changes. The selector-feature safari that led
here — two audio-evidence features measured and rejected — is in
docs/eval.md.

## v0.7.13 — 2026-08-31

Bar one now starts at a real downbeat. The phase of the bar grid used
to be whatever beat the tracker happened to emit first — measurably a
coin toss (it flipped a track's bar structure between two runs of the
same code). The phase is now elected by harmonic rhythm: chords
change on bar lines, so the grid rotation whose bar boundaries carry
the largest chroma change wins. On the meter stand (task 70) the
election hits the beat grid's own quality ceiling wherever the grid
is good. The full A/B behind it — madmom, BeatNet, All-In-One,
accent- and vote-based elections, most of which lost to our own
grid — is in docs/eval.md.

## v0.7.12 — 2026-08-31

The crispness of the old fixed precision selector, kept together
with the adaptive grid's wins. The user's ear was right: a whole
song breathing ONE grid reads tighter than bars that flip between
eighths and sixteenths on transcription jitter. The writer now
computes the base grid the user would have picked by hand (sixteenths
when at least a quarter of the note mass asks for them) and renders
nothing coarser — a coarse bar on the finer nested grid is exact —
while escalation above the base (32nd solo runs, triplet bars) still
demands the picker's evidence. On the user's track the guitar's
stray triplets disappeared entirely and the bar-to-bar grid became
uniform; the 16th-heavy Techno regression stayed healthy.

## v0.7.11 — 2026-08-31

Vocals are out of the score (a product decision): no vocal card, no
vocal note track — vocal transcription fought tonality and
recitative for months and nobody wanted the result. The vocal stem
is still separated (the backing track needs it) and synced lyrics
still run over it — that pair is the seed of a future karaoke mode.
Old saved projects with a vocal part still open and rebuild.

## v0.7.10 — 2026-08-31

The desync war: with the score finally readable, instruments were
audibly drifting against each other. Four real mechanisms found and
fixed in the score writer (docs/eval.md "BAND TIGHTNESS" has the
dissection, including the trace hook that replaced two hours of
metric-artifact chasing): per-model onset latency is calibrated out
per part, attacks of different instruments within 55 ms settle on
one consensus time before any quantization (drums anchor the pocket),
measures share their display grid across tracks so identical moments
round identically, and a rounded-up duration can no longer push the
next attack a slot late. Ground truth after: 92% of shared band hits
land written together, guitar-bass 99.2%; the residue lives in
keys/vocal pairs on cross-family grids. Raw times stay untouched in
parts.json and MIDI exports.

## v0.7.9 — 2026-08-31

The fourth and final face of the "32nd walls": notes BEFORE the
first tracked beat. On a track whose drums enter late the beat grid
started with them (14 s in), every intro note was clamped onto slot
zero and spread one 32nd apiece — a wall on the first screen,
identical at any tempo, immune to every earlier fix (whose diseases
were real, but different). The beat grid now extends backward to the
start of the audio at the opening tempo; the victim's intro renders
as clean eighths after two honest bars of rest, zero 32nds anywhere,
golden means identical. The dissection that found it (pristine model
notes -> clean venv runs -> the frozen app driven headless over its
own API -> all 55 thirty-seconds in bars 1-2) is in docs/eval.md
"THE INTRO CRUSH".

## v0.7.8 — 2026-08-30

The real culprit behind the "32nd walls" on the user's acoustic
track: the TEMPO, not the grid. The beat tracker picked 81 BPM for a
161.5 song, so correct notation read one level too fine (eighths as
sixteenths, sixteenths as thirty-seconds). A tempo-truth stand built
from Suno MIDI meta showed the detector octave-errs on 3 of 8 known
tracks, in both directions. New octave-correction rule after the
detector, from note evidence: material at the beat rate halves the
tempo (generalizes the old keys-only rule, now also in solo mode),
sixteenth-dense material doubles it — but only when the audio's own
periodicity votes for the doubled tempo (36% on the victim vs 0-2%
on true-tempo sixteenth songs). Benched 9/9 on every track with
known tempo; the victim's lead flipped from 603 sixteenths + 99
thirty-seconds to 606 eighths + 6 thirty-seconds. Drum-tracked tempi
get no exemption — the tracker octave-errs on real kits too, and the
three guards are the actual protection. Full dissection in
docs/eval.md "THE TEMPO OCTAVE".

## v0.7.7 — 2026-08-30

Kills the 32nd-note walls on strummed acoustic chords (the second
face of the durations war). A strum spreads a chord's attacks wider
than the chord-grouping window; the stray tail notes then read as
real 32nd structure and whole measures escalated to junk fine grids —
Suno mixes barely strum, which is why the earlier calibration never
saw it. The guitar now gathers strummed chords the way the piano
always has (notes join only while the first still rings, so fast
runs never gather): strummed eighths render as eighths, real 32nd
solo runs still get their 32nds. Measured prices within noise
(GuitarSet fingering −0.007, golden guitar F1 −0.01); full story in
docs/eval.md.

## v0.7.6 — 2026-08-30

Fingering, round two: the error anatomy after v0.7.3 showed whole
phrases landing in the wrong box, 75% of them LOWER on the neck than
the human played — melodies float where chords anchor. A V-shaped
position prior (pull toward the 5th-fret region) plus a re-balanced
cost system fixes exactly that: string-choice agreement with real
players 0.699 → 0.768 on the full GuitarSet corpus, 0.647 → 0.743 on
held-out players (+0.21 cumulative since hand-set weights). Rock
style hits 0.812. Two textbook unit tests were consciously re-sided
with the live players (docs/eval.md task 70 has the full story,
including the two hypotheses that died on the way: beam width and
rest-relocation).

## v0.7.5 — 2026-08-30

The performance batch (docs/perf.md carries every measurement):

- Separation runs on Metal (3.4× on its stage) with an automatic CPU
  retry; `TABFORGE_DEMUCS_DEVICE` overrides.
- The vocal mono-vs-BasicPitch chooser decides on a 60 s probe
  instead of paying full-stem pyin to lose (35 s → ~10 s); bass keeps
  the full-stem chooser — the probe measurably flips its decision the
  wrong way there.
- Gate: full golden eval with both changes — every instrument mean
  identical to the scoreboard.
- The big prize was measured and honestly declined: replacing MT3's
  full pass with sampled windows breaks its instrument attribution
  (long-context model; a clean excerpt of a guitar-rich section hears
  zero guitar) — buried with the full story in docs/eval.md, MT3
  stays the dominant cost by design.

## v0.7.4 — 2026-08-30

Fixes the rhythm mess v0.7.2's adaptive grid made of transcribed
tracks. The per-measure grid picker had hard escalate-on-any-conflict
rules, and transcription onset noise (±50 ms) was enough conflict:
37–66% of transcribed measures escalated into junk 32nds and fake
triplets — rhythm and tempo read as mush. The picker is now
calibrated on data (clean Suno truth MIDI vs our noisy transcriptions
of the same songs): merged attacks are cheap (a lone flam is pushed a
slot, the old behavior) so only a real RUN of fine notes pays for a
finer grid, and triplet grids rent higher than straight ones so
jitter can't masquerade as shuffle. After calibration: junk share on
noisy material 3–17% (what remains is the solo runs that ARE fine
material), clean 16ths still render as 16ths, real 32nd runs and
triplets still escalate (unit-tested).

## v0.7.3 — 2026-08-30

Playable fingering: the Viterbi cost weights were tuned against 360
human GuitarSet performances (the task-65 ruler) instead of being
hand-set. String-choice agreement with real guitarists: 0.598 → 0.699
overall, held-out players 0.531 → 0.647, bossa/jazz (position-heavy
styles) +0.17. The mechanism: the old weights hugged the nut and
hopped to thin strings; the tuned ones make the hand move expensive
and let it PLANT in a position, the way humans play. Full protocol,
weights and the remaining error analysis (phrase context is the next
lever): docs/eval.md, scripts/tune_viterbi.py.

## v0.7.2 — 2026-08-30

The durations war and the editor batch (all four from live feedback).

- **Every note length, everywhere.** The score grid is no longer a
  global choice: each measure of each track now picks the coarsest
  grid that keeps its notes distinct — eighths stay clean eighths, a
  32nd solo run gets real 32nds (it used to collapse onto the coarse
  grid and sound slowed), triplets come out as actual tuplets, in any
  measure, verse or solo. The "rhythm precision" selector is gone —
  it was a crutch around the rigid grid.
- **Group editing.** The note popover grew an "apply to every X"
  checkbox: one stroke moves ALL notes of that pitch to the chosen
  string — across the whole part, or only inside the drag-selected
  bars when a selection is active. Undo restores the previous pins
  verbatim.
- **The screen stays put after an edit.** The score reload used to
  restore the scroll position once, before alphaTab finished its lazy
  render — the page was still short and the position clamped to the
  top. A keeper now holds the position through the whole re-render
  (and hands the wheel back on the first real user gesture).
- Editor note addressing moved to the fine grid (24 units per beat):
  in fast runs a click could land on a neighboring same-pitch note.
- Percussion detection in the player hardened (stave flag, GM channel
  9, part name) — a drums track never shows a tab staff.

## v0.7.1 — 2026-08-30

Two bugs from live testing, both verified in the browser:

- Cyrillic lyrics (or a section renamed in Cyrillic) killed the
  multi-track song.gp5 — the app then showed download cards with no
  score at all. The gp5 writer now falls back cp1252 → cp1251 →
  replace-unencodable; the .lrc keeps the exact text in UTF-8 either
  way. Verified on the failing track: score builds, alphaTab reads
  it (7 tracks, 131 bars), lyrics round-trip intact.
- Loading a second track showed the previous track's score with dead
  clicks: the player was created over the old one without destroying
  it, and the old score kept catching clicks. The player is now torn
  down before every rebuild (two-track browser test passes).

## v0.7.0 — 2026-08-28

Solo mode and the guitar-engine war (tasks 62–66; docs/eval.md has
every number).

### Solo mode — the headline
- A "Solo instrument" switch on the start screen: no separation at
  all — the whole file IS the instrument. Analyze drops from ~3 min
  to seconds of setup, and accuracy hits the offline ceiling exactly
  (solo guitar F1 0.46, solo bass 0.81 — separation used to be pure
  loss here).
- Solo-detect names the instrument by merging both whole-mix models'
  note densities (MT3 + MuScriptor), so a clean guitar one model
  half-hears as "other" is still a guitar; one card arrives
  preselected, the rest stay quiet.
- Backing track and leak filtering are correctly off (there is
  nothing to leak from).

### Guitar engines
- New backend: GAPS (QMUL, MIT code AND weights, runs in-process).
  On acoustic solo guitar it beats everything we have — GuitarSet
  F1 0.858 vs MuScriptor-medium 0.745 vs Basic Pitch 0.590.
- "Guitar engine" dropdown on the instruments screen:
  auto | MuScriptor | GAPS | Basic Pitch. Auto routes by sound —
  acoustic-flavored solo tracks go to GAPS, distorted/electric and
  all mixes stay on MuScriptor (on distorted material GAPS loses
  badly: 0.25 vs 0.53 — measured both ways before routing).
- MuScriptor-medium accepted and routed where it wins the >=0.05
  rule: solo bass 0.68→0.81, solo guitar 0.41→0.46, keys in mixes
  0.60→0.65 (medium's cache now outranks MT3 for keys).
  Instrument-conditioning was measured and buried (max +0.03).

### The fingering ruler
- scripts/eval_guitarset.py: our Viterbi string assignment measured
  against 360 human performances for the first time — 0.598 overall
  (open strings 0.994, fretted 0.569), with a one-directional
  thinner-string/low-fret bias as the diagnosis. Not blind-fixed:
  the number is the baseline for a dedicated cost-tuning task.

### Speed and UX (from live testing)
- MT3 + MuScriptor warm up in parallel with demucs, and MT3 runs on
  Metal (2× faster): analyze wall time ≈ the longest model, not the
  sum — 192 s for a 185 s track.
- Sixteenth-note material is detected from note spacing and the
  rhythm-precision selector pre-set accordingly (the Heaven Burns
  rhythm fix).
- The scrollbar obeys the human: cursor-follow is opt-in (⤓ button),
  any manual scroll disables it, and the chord strip no longer drags
  the page.
- Editing works from the project screen again (the job id was lost
  on direct-to-done pages; editor errors now show as toasts).
- Click-to-hear on virtual instruments: Karplus–Strong pluck for
  frets, additive piano for keys, noise/sine drums.

## v0.6.0 — 2026-08-26

The accuracy war concluded and the guitarist's convenience layer.
Golden-corpus strict F1, first honest baseline -> now: guitar
0.24 -> 0.41, bass 0.34 -> 0.63, keys (broken) -> 0.58, drums
0.38 -> 0.55, vocals 0.13 -> 0.15 + honest dead-note crosses.

### Accuracy (tasks 52-57, docs/eval.md has every measurement)
- Honest evaluation ruler: per-instrument alignment (time x octave),
  pitch-only column, per-file offset discovery in Suno exports.
- Monophonic bass/vocals path (pyin + onset segmentation) with a
  density chooser falling back to Basic Pitch on dirty stems;
  recitative vocals become dead-note crosses instead of fake pitches.
- MT3 instrument-presence arbiter on the analyze cards
  (found / absent / uncertain) with content-match guards; optional
  install at ~/mt3.
- Note-source routing: keys from MT3, guitar+bass from MuScriptor
  (optional non-commercial install), drums and vocals stay native.
- The pre-export snap is gone (the gp5 writer slots notes itself):
  keys 0.27 -> 0.44, Loken bass 0.41 -> 0.61 on golden.
- Phantom drums can no longer drive the tempo (envelope crest gate);
  keys-led drumless tracks auto-suggest half time (152 -> 81 BPM on
  the piano golden, sheet says 73-82).
- Softer guitar Basic Pitch preset: +0.10 F1 on the metal golden.

### Convenience (tasks 55, 58-60)
- Mass editor: drag-select bars -> octave shift / delete / move to
  another instrument / collapse octave doubles; pins and legato
  survive.
- Review mode: per-note confidence, disputed notes highlighted and
  steppable.
- Reference export: corrected notes leave as per-instrument MIDI
  named like the golden corpus — your edits become ground truth.
- Chord line: names (power chords and slash basses included) above
  the score, synced to the cursor, fret diagrams from the actual tab
  shapes, labels in the gp5.
- Song sections: chroma novelty + chord-loop-break voting, colored
  timeline, click to jump, rename inline (gp5 markers follow).
- Synced lyrics (tabforge[lyrics], faster-whisper): running
  word-highlighted line, junk-word marking with one-click hide,
  .lrc export and the gp5 lyrics channel.
- MT3 arbiter checkbox, chords/sections and lyrics checkboxes with a
  language override on the start screen.

## v0.5.0 — 2026-08-25

Post-feedback polish (tasks 33-38 + the rhythm war): beat repair and
smoothing, rhythm precision selector, fretboard editor with
alternatives and approve/revert, 24-fret neck with note names and
chord labels, grand staff for keys, 88-key piano, project save/load
(.tabforge), spacebar transport, tab-only guitar view.

## v0.4.0 — 2026-08-25

The drum track (9-voice spectral classifier), desktop app rebuild,
tick-based export, instrument profiles, two-step analyze/transcribe
flow, the project screen and the unified multi-track player, the
note editor.

## v0.3.0 — 2026-08-25

Phases 5 and 6: the server grew up and moved out, and the app fits in
a pocket.

### Server, ready for the network
- Job lifecycle: finished jobs expire (TTL) with background cleanup,
  the store is capped with oldest-finished eviction and an honest 429.
- Input limits and validation: streaming upload size check, duration
  probe before the pipeline, real-audio validation, filename traversal
  killed (only the extension survives), readable 4xx errors in the UI.
- Optional API token (TABFORGE_TOKEN) via header or ?token= for
  downloads; the UI asks once. Configurable workers (TABFORGE_WORKERS).
- Non-wav input is re-encoded to wav before demucs — real-world mp3s
  with malformed frames no longer crash the strict decoders.

### Deployment
- Dockerfile (python 3.11-slim, ffmpeg, non-root, uvicorn) and compose
  with a model-cache volume; all knobs overridable from the host env.
  Measured: ~1.5 min for a 3-minute track in a 4-CPU container, cold
  start costs nothing extra on a fast connection.
- Cloudflare quick-tunnel service under the `tunnel` compose profile:
  free public URL, no account or public IP; token required.

### Mobile (PWA)
- manifest (standalone, dark walnut theme, generated lamp icons) and a
  version-keyed service worker: instant shell start from cache, /api/*
  always live, old shells purged on deploy.
- Mobile UI pass: 44px+ touch targets, tap-to-pick on the drop zone,
  the ASCII tab scrolls inside its container.

### Fixes & maintenance
- Frontend poll retries with backoff instead of dying on one hiccup;
  key detection failures degrade instead of killing the job; the tempo
  source falls back to the mix when the drums stem is silent.
- Review issues #1–#4 closed (subdivision & time signature wired into
  gp5, single audio decode + pruned tempo hypotheses, lazy alphaTab
  player); #5/#6 deliberately deferred; #7/#8 track the remaining
  hosting and offline tails.

## v0.2.0 — 2026-08-25

The first release where the whole path — audio in, playable tablature
out — works end to end and is verified by tests at every joint.

### Transcription & pipeline
- Tuned Basic Pitch thresholds for Suno-style guitar (fewer ghost
  fragments, chords hold together); ghost-overtone cleanup via minimum
  note length and a polyphony cap.
- Stable tempo: detected once per track from the drums stem (with an
  RMS audibility check falling back to the full mix), tempo-multiple
  disambiguation via weighted candidate families, and a sanity guard
  (fewer than 8 beats or BPM outside 40–260 → honest 120 BPM fallback
  flagged "tempo: estimated poorly").
- Key detection (Krumhansl-Schmuckler over chroma_cqt); failures
  degrade to "unknown key" instead of killing the job.
- Optional lead/rhythm guitar split (`--split-guitars` / UI checkbox):
  chords and their neighborhood go to rhythm, high single-note runs to
  lead; each part becomes its own track and card.
- Pitch-bend trajectories are kept per note and classified into
  slide / bend / vibrato; legato pairs (hammer-on / pull-off) are
  detected and rewarded in the fingering when they fit on one string.

### Fingering core
- Unplayable leading events (transcription noise) no longer wipe out
  the whole tab.
- Legato-aware transition costs; all behavior remains tunable via
  `TabConfig`.

### Export
- gp5: correct Beat/Voice structure (alphaTab-compatible), positional
  measure assembly with rest filling, measures anchored at the first
  detected beat, key signature in every measure header, time signature
  (n/4) and grid subdivision (including triplets) written through,
  articulation effects (hammer, slides, bends in quarter-tone units,
  vibrato).
- ASCII tab: articulations drawn as `5h7`/`7p5`, `/` and `\`, `~`.
- MusicXML: tempo and key signature.
- `scripts/check_gp5.py`: round-trip verification of pitches, note
  positions, per-measure key signatures, and effects.

### App
- Web UI: per-part Play button with a lazily loaded alphaTab synth
  (soundfont fetched on first click), job polling with retry/backoff,
  warnings surfaced next to BPM/key.
- Desktop: PyInstaller build → `dist/TabForge.app`; demucs runs
  strictly in a subprocess (failures surface as job errors instead of
  hanging jobs), models download to `~/.cache` on first run.

### Reliability & testing
- 93 tests (was 18): unit, gp5 round-trip, and an end-to-end synthetic
  fixture corpus (short clip, leading silence, drumless track,
  ground-truth scale, silence).
- A multi-agent code review produced 17 findings: all fixed or filed
  as issues (#1–#4 fixed, #5 deferred).

### Project
- Everything translated to English; Python 3.11 requirement documented
  (basic-pitch has no macOS support on 3.12+).

## v0.1.0

Initial skeleton: fingering core (Viterbi over hand positions),
pipeline, FastAPI server, pywebview desktop shell, frontend, CI.
