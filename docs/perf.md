# Pipeline performance (task 68, measured 2026-08-30)

Apple M-series (this dev machine), v0.7.4 code, all instruments
picked, chords+lyrics on. Harness: scratchpad profile_pipeline.py —
thread-safe timers monkeypatched around every heavy function, three
tracks of different lengths, no competing load. Stages OVERLAP (the
mix models warm in parallel with demucs), so stage sums can exceed
the analyze wall; shares below are stage-time / total wall.

## Wall times

| track          | length | analyze | transcribe | total | ×realtime |
|----------------|-------:|--------:|-----------:|------:|----------:|
| Fulgrim (wav)  |   66 s |   113 s |       24 s | 137 s |      2.1× |
| Techno (mp3)   |  228 s |   283 s |      119 s | 402 s |      1.8× |
| Hero (mp3)     |  384 s |   563 s |       86 s | 649 s |      1.7× |

## Stage shares (of total wall)

| stage                | Fulgrim     | Techno      | Hero        |
|----------------------|------------:|------------:|------------:|
| MT3 whole-mix        | 99.6 s  73% | 162 s   40% | 398 s   61% |
| MuScriptor whole-mix | 11.0 s   8% | 119 s   30% | 159 s   25% |
| whisper lyrics       |       —     | 70 s    17% | 8 s      1% |
| mono f0 (pyin)       | 20.2 s  15% | 35 s     9% | 59 s     9% |
| demucs separation    | 5.8 s    4% | 20 s     5% | 28 s     4% |
| Basic Pitch (all)    | 6.0 s    4% | 6 s      2% | 6 s      1% |
| musicxml export      | 1.3 s    1% | 3 s      1% | 4 s      1% |
| everything else*     |     < 2 s   |     < 2 s   |     < 3 s   |

*tempo/beats, PANNs tagger, chords, sections, drums classifier,
Viterbi fingering, gp5/midi exports — all sub-second to ~1 s each.
The caches work: the second MT3/MuScriptor call per track is 0.0 s.

**The pipeline is an MT3 story.** 40–73% of every run is the
whole-mix MT3 pass (0.7–1.5× realtime on Metal, density-dependent);
MuScriptor adds 25–30% on real mixes. Demucs — the usual suspect —
is 4–5%. Everything after separation+models (fingering, quantize,
all exports, chords, sections) is a rounding error: the "math" half
of the pipeline is effectively free.

## Metal audit (does it SAY MPS and RUN MPS?)

| branch          | device | evidence |
|-----------------|--------|----------|
| MT3             | MPS ✓  | `_mt3_run.py` explicit, CPU fallback wrapped in try |
| MuScriptor      | MPS ✓  | library default (`accelerator.current_accelerator()`, fp16 on MPS) |
| GAPS            | MPS ✓  | prints "Using mps for inference" |
| demucs          | **CPU** | we pass no `-d`; demucs defaults to CPU on macOS |
| PANNs tagger    | **CPU** | `device="cpu"` hardcoded ×2 in tagging.py (1.5 s/track — harmless) |
| whisper         | CPU    | CTranslate2 has no MPS backend — no lever exists |
| Basic Pitch     | CoreML | the bundled CoreML model path |

demucs on MPS measured (Fulgrim, `-d mps`): **5 s vs 17 s CPU**
(3.4×), stems healthy (rms parity, cpu↔mps waveform correlation
0.986). Worth switching, but it saves only ~20 s on a 6-minute track
— the win is real yet cosmetic next to MT3.

## MT3: first 90 seconds vs the whole track

Hero (384 s): full-track MT3 **398 s**; first 90 s **21 s** — 19×
less (shorter decode + the fixed startup amortized). Routing
implication before acting on this: with muscriptor-medium cached,
MT3's remaining jobs are the presence arbiter and solo-detect —
both sampling tasks a 90 s window could serve. BUT for installs
without MuScriptor, keys take their NOTES from mt3.mid, and a 90 s
pass would truncate the keys transcription. A future "fast analyze"
must either keep the full pass when MT3 is the note source, or
run 90 s first for verdicts and finish the full pass lazily.

## The cost of instruments the user did not pick

Small. Analyze samples every stem's 30 s window through Basic Pitch
regardless of the eventual pick: 0.4–1.7 s per stem, 2–3 s per
track total. The REAL waste found instead: the mono-vs-BP density
chooser runs pyin over the FULL stem to decide who transcribes —
on Techno that was 35 s of pyin for a vocal stem that then went to
Basic Pitch (1.6 s) anyway. The chooser could decide on a 30–60 s
sample for ~5× less.

## Recommendations (each is its own measured task)

1. **MT3 fast-analyze** (the only change that moves the needle):
   90 s arbiter window when MT3 is not the note source → analyze
   wall on Hero drops ~560 s → ~180 s (MuScriptor becomes the
   critical path). Gate: arbiter verdicts must not change on the
   golden tracks.
2. demucs `-d mps` with CPU fallback: free 3.4× on its 4–5%.
3. Mono chooser on a sample, not the full stem: −20–50 s/track.
4. Leave alone: whisper (no MPS exists), PANNs (1.5 s), exports,
   fingering, chords/sections (all noise).
