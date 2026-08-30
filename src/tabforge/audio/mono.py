"""
Monophonic transcription path (task 53).

Basic Pitch is polyphonic by design: on a bass stem it happily reports
a note AND its octave twin (42-45% of est bass notes have a ±12
time-overlapping twin on the golden corpus), and on semi-recitative
vocals it invents pitches where there is only rhythmic speech. A
monophonic f0 tracker kills octave splits BY CONSTRUCTION — it must
choose one pitch per frame.

The path: f0 track (pyin from librosa always available; torchcrepe
used when installed — MIT code AND weights, bundled in the wheel) →
segmentation by onset envelope → one note per segment from the median
voiced f0. The recitative rule for vocals: a segment only becomes a
PITCHED note when f0 holds within a semitone for >= stable_ms;
energetic but unstable segments become dead notes (rhythmic crosses
in gp5) — an honest "something is sung here" beats a lying pitch.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..core import NoteEvent

HOP = 256
SR = 22050


def _frame_length(fmin: float) -> int:
    """pyin analysis window: ~8 periods of fmin, power of two.

    Bass (fmin 26) needs 4096 samples to resolve A0-region pitch, but
    a wide window on vocals would SMOOTH real speech pitch movement
    into a false 'stable' f0 — the recitative rule would never fire.
    Vocals at fmin 70 get 2048 (93 ms), librosa's own default pairing
    for a C2 floor."""
    need = 4 * SR / fmin
    n = 2048
    while n < need and n < 16384:
        n *= 2
    return n

# Per-stem parameters for the mono path (thresholds are stand-tuned).
MONO_PRESETS: dict[str, dict] = {
    "bass": dict(fmin=26.0, fmax=500.0, recitative=False),
    # stable_ms 100: pyin's ~93 ms window smooths ANY speech into
    # >=50 ms quasi-plateaus, so a recitative gate must demand a hold
    # longer than the window itself; sung recitative notes run longer
    "vocals": dict(fmin=70.0, fmax=1100.0, recitative=True,
                   stable_ms=100.0),
}


def _f0_pyin(y: np.ndarray, fmin: float, fmax: float
             ) -> tuple[np.ndarray, np.ndarray]:
    import librosa

    f0, _flag, vprob = librosa.pyin(
        y, fmin=fmin, fmax=fmax, sr=SR,
        frame_length=_frame_length(fmin), hop_length=HOP, fill_na=np.nan)
    return f0, vprob


def transcribe_mono(
    audio: Path,
    *,
    fmin: float,
    fmax: float,
    recitative: bool = False,
    stable_ms: float = 50.0,
    min_note_ms: float = 60.0,
) -> list[NoteEvent]:
    """One monophonic stem -> notes (plus dead notes in recitative mode)."""
    import librosa

    y, _sr = librosa.load(str(audio), sr=SR, mono=True)
    if not len(y):
        return []
    f0, _conf = _f0_pyin(y, fmin, fmax)
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=HOP)[0]
    n = min(len(f0), len(rms))
    f0, rms = f0[:n], rms[:n]
    frame_s = HOP / SR

    onsets = librosa.onset.onset_detect(
        y=y, sr=SR, hop_length=HOP, backtrack=True, units="frames")
    bounds = sorted({0, n} | {int(o) for o in onsets if 0 < o < n})
    midi = 69 + 12 * np.log2(f0 / 440.0)   # nan where unvoiced

    floor = np.median(rms[rms > 0]) if np.any(rms > 0) else 0.0
    peak = float(rms.max()) or 1.0
    min_frames = max(2, int(min_note_ms / 1000 / frame_s))
    stable_frames = max(2, int(np.ceil(stable_ms / 1000 / frame_s)))
    hold = max(2, int(0.08 / frame_s))

    def _split_by_pitch(a: int, b: int) -> list[tuple[int, int]]:
        """Sub-notes inside one onset segment: break where the pitch
        moves >= 0.8 semitone and STAYS (melisma/legato — a new note
        without a new attack)."""
        parts, run_start, ref, i = [], a, np.nan, a
        while i < b:
            v = midi[i]
            if np.isfinite(v):
                if not np.isfinite(ref):
                    ref = v
                elif abs(v - ref) >= 0.8:
                    ahead = midi[i:min(i + hold, b)]
                    moved = ahead[np.isfinite(ahead)]
                    if len(moved) >= hold // 2 and np.all(
                            np.abs(moved - ref) >= 0.6):
                        parts.append((run_start, i))
                        run_start, ref = i, v
                    else:
                        i += len(ahead) or 1
                        continue
                else:
                    ref = 0.9 * ref + 0.1 * v   # drift with vibrato
            i += 1
        parts.append((run_start, b))
        return parts

    def _longest_plateau(a: int, b: int) -> int:
        """Longest voiced run (frames) whose total pitch span stays
        within one semitone — 'is any pitch actually HELD here?'"""
        best, i = 0, a
        while i < b:
            if not np.isfinite(midi[i]):
                i += 1
                continue
            j = i
            while j < b and np.isfinite(midi[j]):
                j += 1
            run = midi[i:j]
            for s in range(len(run)):
                if len(run) - s <= best:
                    break
                lo = hi = run[s]
                for e in range(s + 1, len(run)):
                    lo, hi = min(lo, run[e]), max(hi, run[e])
                    if hi - lo > 1.0:
                        break
                    if e - s + 1 > best:
                        best = e - s + 1
            i = j
        return best

    def _emit(a: int, b: int, dead: bool) -> None:
        seg = midi[a:b]
        voiced = np.isfinite(seg)
        if not voiced.any():
            return
        vi = np.flatnonzero(voiced)
        med = float(np.median(seg[voiced]))
        start = (a + vi[0]) * frame_s
        dur = max((vi[-1] - vi[0] + 1) * frame_s, min_note_ms / 1000)
        loud = float(rms[a:b].mean())
        vel = int(np.clip(40 + 70 * loud / peak, 30, 115))
        notes.append(NoteEvent(int(round(med)), start, dur, vel, dead=dead))

    notes: list[NoteEvent] = []
    # the note/cross decision is made PER SYLLABLE (onset segment):
    # only a segment that actually holds some pitch gets split into
    # pitched sub-notes; a voiced glide with no held pitch is speech
    for a, b in zip(bounds[:-1], bounds[1:]):
        if b - a < min_frames:
            continue
        seg = midi[a:b]
        voiced = np.isfinite(seg)
        loud = float(rms[a:b].mean())
        if voiced.sum() >= min_frames // 2:
            if _longest_plateau(a, b) >= stable_frames:
                for sa, sb in _split_by_pitch(a, b):
                    sv = np.isfinite(midi[sa:sb]).sum()
                    if sb - sa >= min_frames and sv >= min_frames // 2:
                        _emit(sa, sb, dead=False)
                continue
            if recitative:
                _emit(a, b, dead=True)
            elif (b - a) * frame_s >= 0.12:
                # a long unstable stretch outside recitative mode is a
                # real slide/deep vibrato: the median is still the note
                _emit(a, b, dead=False)
            continue
        # no usable f0 at all: in recitative mode an energetic burst is
        # still an EVENT (spoken syllable) — mark it, don't invent pitch
        if recitative and loud > 1.5 * floor:
            prev = notes[-1].pitch if notes else 60
            vel = int(np.clip(40 + 70 * loud / peak, 30, 115))
            notes.append(NoteEvent(prev, a * frame_s,
                                   max((b - a) * frame_s, min_note_ms / 1000),
                                   vel, dead=True))
    return notes
