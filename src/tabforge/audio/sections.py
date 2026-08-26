"""
Song structure (task 59): intro / verse / chorus / bridge / outro.

Two voices vote on the boundaries:
1. AUDIO — a checkerboard novelty curve over the beat-synced chroma
   self-similarity matrix (the classic structure-analysis recipe);
2. HARMONY — the chord line from task 58: a bar where the running
   chord pattern changes votes for a boundary. Neither voice alone is
   reliable on dense mixes; their sum is peaked, snapped to bar lines.

Labels come from clustering the segments by mean chroma: repeated
material forms clusters, the LOUDEST repeated cluster is the chorus,
the most frequent other one the verse; unique segments become intro /
bridge / outro by position. The names are proposals — the UI lets the
human rename everything (our philosophy: automation proposes, the
human refines).

Features are computed at ANALYZE (the mix is at hand there) and
cached; boundary detection runs at TRANSCRIBE where the chords exist.
"""

from __future__ import annotations

from pathlib import Path


def compute_features(mix: Path, beats: list[float], out_path: Path) -> None:
    """Beat-synced chroma + RMS of the mix, cached as .npz."""
    import librosa
    import numpy as np

    y, sr = librosa.load(str(mix), mono=True)
    if not len(y) or len(beats) < 4:
        return
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    rms = librosa.feature.rms(y=y)[0]
    frames = librosa.time_to_frames(beats, sr=sr)
    frames = frames[(frames >= 0) & (frames < chroma.shape[1])]
    if len(frames) < 4:
        return
    chroma_sync = librosa.util.sync(chroma, frames)
    rms_sync = librosa.util.sync(rms[None, :], frames)[0]
    np.savez(str(out_path), chroma=chroma_sync, rms=rms_sync)


def _novelty(chroma) -> "np.ndarray":
    """Checkerboard-kernel novelty over the cosine self-similarity."""
    import numpy as np

    x = chroma / (np.linalg.norm(chroma, axis=0, keepdims=True) + 1e-9)
    S = x.T @ x
    n = S.shape[0]
    L = 8                                  # beats each side of the kernel
    nov = np.zeros(n)
    for i in range(n):
        a, b = max(0, i - L), min(n, i + L)
        past = S[a:i, a:i]
        future = S[i:b, i:b]
        cross = S[a:i, i:b]
        if past.size and future.size and cross.size:
            nov[i] = past.mean() + future.mean() - 2 * cross.mean()
    if nov.max() > 0:
        nov /= nov.max()
    return nov


def detect_sections(features_path: Path, beats: list[float],
                    beats_per_measure: int,
                    chords: list[dict] | None = None) -> list[dict]:
    """Sections as [{start, end, label}] (seconds; label editable)."""
    import numpy as np

    if not features_path.exists() or len(beats) < 2 * beats_per_measure:
        return []
    data = np.load(str(features_path))
    chroma, rms = data["chroma"], data["rms"]
    n = min(chroma.shape[1], len(rms), len(beats) - 1)
    if n < 2 * beats_per_measure:
        return []
    chroma, rms = chroma[:, :n], rms[:n]

    nov = _novelty(chroma)

    # the harmony's vote: a broken chord LOOP, not a mere chord change
    # (a fast harmonic rhythm changes chords every bar — voting on
    # each change drowned the novelty curve: 109 votes on Loken). The
    # loop period is estimated per track; inside a looping section the
    # pattern repeats and the vote stays silent.
    bar = beats_per_measure
    harm = np.zeros(n)
    if chords:
        bar_chord = []
        for k in range(0, n, bar):
            t = beats[k]
            name = None
            for c in chords:
                if c["start"] <= t + 1e-6:
                    name = c["name"]
                else:
                    break
            bar_chord.append(name)
        nb = len(bar_chord)
        if nb >= 8:
            period = min((2, 3, 4), key=lambda p: sum(
                bar_chord[k] != bar_chord[k - p]
                for k in range(p, nb)))
            for k in range(period, nb):
                if bar_chord[k] != bar_chord[k - period]:
                    harm[k * bar] = 1.0
    score = nov + 0.7 * harm

    # peaks snapped to bar lines: real sections run 8+ bars, and a
    # song has a handful of them — cap the count so dense material
    # (metal novelty is spiky everywhere) can't shatter into confetti
    bar = beats_per_measure
    min_gap = 8 * bar
    max_bounds = max(3, n // (8 * bar))
    bar_scores = [(i, score[max(0, i - 1):i + 2].max())
                  for i in range(bar, n - bar // 2, bar)]
    bar_scores.sort(key=lambda t: -t[1])
    cut = float(np.mean(score) + np.std(score))
    bounds: list[int] = []
    bound_score: dict[int, float] = {}
    for i, s in bar_scores:
        if s < cut or len(bounds) >= max_bounds:
            break
        if all(abs(i - j) >= min_gap for j in bounds):
            bounds.append(i)
            bound_score[i] = s
    bounds = sorted({0, *bounds, n})

    # segment descriptor: the bar-by-bar chroma SEQUENCE of the
    # opening 8 bars. A mean over a whole segment converges to the key
    # profile — every segment of a one-key song looks identical that
    # way (measured: all three golden tracks collapsed into a single
    # cluster). Openings are what repeats recognizably.
    segs = list(zip(bounds[:-1], bounds[1:]))
    descs, louds = [], []
    for a, b in segs:
        vecs = []
        for k in range(8):
            ba, bb = a + k * bar, min(a + (k + 1) * bar, b)
            if ba >= b:
                vecs.append(np.zeros(12))
                continue
            m = chroma[:, ba:bb].mean(axis=1)
            vecs.append(m / (np.linalg.norm(m) + 1e-9))
        d = np.concatenate(vecs)
        descs.append(d / (np.linalg.norm(d) + 1e-9))
        louds.append(float(rms[a:b].mean()))
    letters: list[int] = []
    reps: list[np.ndarray] = []
    for d in descs:
        for li, r in enumerate(reps):
            if float(d @ r) > 0.85:
                letters.append(li)
                break
        else:
            reps.append(d)
            letters.append(len(reps) - 1)

    # adjacent segments of the same material merge — but only across
    # a WEAK boundary. A strong novelty peak between two same-letter
    # segments is a real musical change the chroma letters can't see
    # (a riff change inside one harmonic fabric — the metal case).
    strong = cut * 1.5
    merged: list[tuple[int, int, int]] = []      # (a, b, letter)
    for (a, b), li in zip(segs, letters):
        if (merged and merged[-1][2] == li
                and bound_score.get(a, 0.0) < strong):
            merged[-1] = (merged[-1][0], b, li)
        else:
            merged.append((a, b, li))

    loud_of = {li: float(np.mean([louds[k] for k, l in enumerate(letters)
                                  if l == li]))
               for li in set(letters)}
    counts = {li: sum(1 for *_, l in merged if l == li)
              for li in set(letters)}
    repeated = sorted((li for li, c in counts.items() if c >= 2),
                      key=lambda li: -loud_of[li])
    # a chorus must not only repeat — it must stand OUT: louder than
    # the other repeated material. One homogeneous cluster (a metal
    # wall) is just the song's fabric: that's Verse.
    chorus = verse = None
    if len(repeated) >= 2 and loud_of[repeated[0]] > 1.08 * loud_of[repeated[-1]]:
        chorus = repeated[0]
        verse = max((li for li in repeated if li != chorus),
                    key=lambda li: counts[li])
    elif repeated:
        verse = repeated[0]

    out = []
    for k, (a, b, li) in enumerate(merged):
        if li == chorus:
            label = "Chorus"
        elif li == verse:
            label = "Verse"
        elif k == 0:
            label = "Intro"
        elif k == len(merged) - 1:
            label = "Outro"
        else:
            label = "Bridge"
        out.append({"start": float(beats[a]),
                    "end": float(beats[b] if b < len(beats)
                                 else beats[-1]),
                    "label": label})
    return out
