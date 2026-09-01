"""
Bend rescue (calibration session 1, case #4: "соло в ритме и нет
бендов" — bar 62 of Casey).

The routed transcribers (MuScriptor, MT3) emit ZERO pitch-bend data
(measured), so a bent note arrives flat and the score never shows the
bend. This pass reads the bend back off the audio: for each guitar
note, track f0 in a tight band around the note's own pitch (pyin
constrained to -2..+5 semitones — on a polyphonic stem the tight band
locks onto the note's partial region); a clean voiced contour whose
excursion reaches BEND_MIN semitones becomes NoteEvent.bends, and the
existing articulation pipeline (classify_articulation -> gp5 bend /
vibrato / slide) does the notating.

Guards, honest by construction:
- only notes with NO bends yet (Basic Pitch already supplies real
  contours) and enough length to carry one (>= MIN_DUR);
- >= VOICED_MIN of frames must be voiced (a chord wall gives pyin
  garbage — skip);
- the contour must START on the note (first frames within ON_TOL of
  the nominal pitch) or pyin locked onto a different voice.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

BEND_MIN = 0.45          # semitone excursion that counts as a bend
MIN_DUR = 0.18           # a shorter note cannot carry a notated bend
VOICED_MIN = 0.6         # share of voiced pyin frames required
ON_TOL = 0.45            # first frames must sit on the note itself
MAX_WIN = 1.2            # seconds of contour per note, cap


def annotate_bends(notes: Sequence, wav: Path) -> int:
    """Fill .bends from the stem audio (in place); returns how many
    notes gained a contour."""
    todo = [n for n in notes
            if not n.bends and not n.dead and n.duration >= MIN_DUR]
    if not todo:
        return 0
    import librosa
    import numpy as np

    y, sr = librosa.load(str(wav), sr=22050, mono=True)
    if not len(y):
        return 0
    gained = 0
    for n in todo:
        s0 = int(n.start * sr)
        s1 = int((n.start + min(n.duration, MAX_WIN)) * sr)
        seg = y[s0:s1]
        if len(seg) < 2048:
            continue
        f_nom = 440.0 * 2 ** ((n.pitch - 69) / 12)
        try:
            f0, _vflag, vprob = librosa.pyin(
                seg, sr=sr,
                fmin=f_nom * 2 ** (-2 / 12),
                fmax=f_nom * 2 ** (5 / 12),
                frame_length=1024, hop_length=256)
        except Exception:  # noqa: BLE001 — never fatal
            continue
        voiced = ~np.isnan(f0)
        if voiced.mean() < VOICED_MIN:
            continue
        dev = np.full(len(f0), np.nan)
        dev[voiced] = 12 * np.log2(f0[voiced] / f_nom)
        # forward-fill the unvoiced gaps so the contour stays smooth
        last = 0.0
        out = []
        for d in dev:
            if not np.isnan(d):
                last = float(d)
            out.append(last)
        head = [d for d in out[:4]]
        if not head or max(abs(d) for d in head) > ON_TOL:
            continue                      # locked onto a different voice
        if max(abs(d) for d in out) < BEND_MIN:
            continue                      # flat note — leave it alone
        n.bends = [round(float(d), 3) for d in out]
        gained += 1
    return gained


RUN_MIN = 0.35           # a recoverable voice must sustain this long
RUN_PITCH_MIN = 55       # solo register only — never invent bass
RUN_GAP = 0.12           # unvoiced gap that still continues a run
MATCH_ST = 1.5           # a transcribed note this close = not missing


def _second_look(y, sr, t0: float, t1: float, below_midi: float):
    """pyin over one run's window, constrained ABOVE a clashing voice
    (+6..+19 semitones). Returns (pitch, deviations) or None."""
    import librosa
    import numpy as np

    seg = y[int(t0 * sr):int(t1 * sr)]
    if len(seg) < 2048:
        return None
    f_lo = 440.0 * 2 ** ((below_midi + 6 - 69) / 12)
    f_hi = 440.0 * 2 ** ((below_midi + 19 - 69) / 12)
    try:
        f0, _v, _p = librosa.pyin(seg, sr=sr, fmin=f_lo, fmax=f_hi,
                                  frame_length=2048, hop_length=256)
    except Exception:  # noqa: BLE001
        return None
    voiced = ~np.isnan(f0)
    if voiced.mean() < 0.5:
        return None
    midis = 12 * np.log2(f0[voiced] / 440) + 69
    if np.percentile(midis, 90) - np.percentile(midis, 10) > 2.5:
        return None                       # unstable — harmonic junk
    pitch = int(round(float(np.median(midis))))
    # harmonic guard by ONSET INDEPENDENCE: a real upper voice has
    # its own attack (the Casey solo enters 0.11 s before the chord
    # under it); a 2nd harmonic rises in lockstep with its
    # fundamental. Compare band-energy onset times over the run with
    # a short pre-roll.
    pre = int(0.3 * sr)
    a0 = max(0, int(t0 * sr) - pre)
    win = y[a0:int(t1 * sr)]
    if len(win) < 2048:
        return None
    hop2 = 256
    spec = np.abs(librosa.stft(win, n_fft=2048, hop_length=hop2))
    freqs = np.fft.rfftfreq(2048, 1 / sr)

    def band_env(m):
        f = 440.0 * 2 ** ((m - 69) / 12)
        band = (freqs > f * 0.96) & (freqs < f * 1.04)
        return spec[band].max(axis=0) if band.any() else None

    def onset_of(env):
        idx = int(np.argmax(env >= 0.5 * env.max()))
        return a0 / sr + idx * hop2 / sr

    up_env = band_env(pitch)
    low_env = band_env(below_midi)
    if up_env is None or low_env is None or low_env.max() < 1e-9:
        return None
    if up_env.max() < 0.1 * low_env.max():
        return None                       # too quiet — leakage, not a voice
    if abs(onset_of(up_env) - onset_of(low_env)) < 0.06:
        return None                       # locked attacks = harmonic
    dev = [round(float(12 * np.log2(f / 440) + 69 - pitch), 3)
           if not np.isnan(f) else 0.0 for f in f0]
    return pitch, dev


def rescue_missing_notes(notes: list, wav: Path,
                         progress=lambda *_: None) -> int:
    """Case #4 of calibration session 1 ("нет бендов", Casey 144 s):
    a BENT solo note often vanishes from the routed transcription
    entirely — the stem carries a clean sustained f0 the MIDI simply
    does not have. Recover it: pyin the stem, take voiced runs of
    >= RUN_MIN seconds in the solo register, and where NO transcribed
    note overlaps within MATCH_ST semitones, insert the note WITH its
    contour as bends. Appends to `notes` in place; returns how many."""
    import librosa
    import numpy as np

    from ..core.fretboard import NoteEvent

    y, sr = librosa.load(str(wav), sr=22050, mono=True)
    if not len(y):
        return 0
    hop = 512
    f0, _v, _p = librosa.pyin(y, sr=sr, fmin=110, fmax=1300,
                              frame_length=2048, hop_length=hop)
    times = librosa.times_like(f0, sr=sr, hop_length=hop)
    step = hop / sr

    # a run BREAKS on a pitch jump too: pyin hops between chord
    # harmonics on a polyphonic stem, and gluing the hops into one
    # "note" produced junk at the top of the register with 8-12
    # semitone "bends" (measured on Casey before this guard)
    midi_track = 12 * np.log2(f0 / 440) + 69
    runs: list[tuple[int, int]] = []
    start = None
    gap = 0
    prev_m = None
    for i, f in enumerate(f0):
        if not np.isnan(f):
            m = midi_track[i]
            if start is not None and prev_m is not None \
                    and abs(m - prev_m) > 0.8:
                runs.append((start, i - 1))
                start = i
            elif start is None:
                start = i
            prev_m = m
            gap = 0
        elif start is not None:
            gap += 1
            if gap * step > RUN_GAP:
                runs.append((start, i - gap))
                start, gap, prev_m = None, 0, None
    if start is not None:
        runs.append((start, len(f0) - 1))

    added = 0
    for a, b in runs:
        if (b - a) * step < RUN_MIN:
            continue
        seg = f0[a:b + 1]
        voiced = seg[~np.isnan(seg)]
        if not len(voiced):
            continue
        midi = 12 * np.log2(np.median(voiced) / 440) + 69
        pitch = int(round(midi))
        if pitch < RUN_PITCH_MIN:
            continue
        t0, t1 = times[a], times[b]
        clash = any(n.start < t1 and t0 < n.end
                    and abs(n.pitch - midi) <= MATCH_ST
                    for n in notes)
        if clash:
            # the flagged Casey case (bar 62): the full-track tracker
            # locks onto the LOUD transcribed voice while the solo
            # rings an octave up — a second look constrained ABOVE the
            # clash finds it or nothing does
            up = _second_look(y, sr, t0, t1, midi)
            if up is None:
                continue
            pitch, dev = up
            still = any(n.start < t1 and t0 < n.end
                        and abs(n.pitch - pitch) <= MATCH_ST
                        for n in notes)
            if still or max(abs(d) for d in dev) > 2.5:
                continue
            moving = max(abs(d) for d in dev) >= 0.3
            notes.append(NoteEvent(pitch, float(t0), float(t1 - t0),
                                   velocity=96,
                                   bends=dev if moving else []))
            added += 1
            continue
        dev = [round(float(12 * np.log2(f / 440) + 69 - pitch), 3)
               if not np.isnan(f) else 0.0 for f in seg]
        if max(abs(d) for d in dev) > 2.5:
            continue        # no real bend exceeds this — harmonic junk
        # a stable flat run is a MISSED PLAIN NOTE; keep the contour
        # only when it actually moves (bend/vibrato/slide territory)
        moving = max(abs(d) for d in dev) >= 0.3
        notes.append(NoteEvent(pitch, float(t0), float(t1 - t0),
                               velocity=96,
                               bends=dev if moving else []))
        added += 1
    if added:
        notes.sort(key=lambda n: n.start)
        progress("transcribe",
                 f"bend rescue: {added} missed sustained note(s) "
                 "recovered from the stem")
    return added
