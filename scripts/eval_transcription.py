"""
The transcription quality stand: numbers instead of feelings.

Ground-truth pieces are BUILT, not recorded: note lists defined here are
rendered per instrument with simple synthesis (Karplus-Strong strings,
attack-ramped saws for brass, detuned saws for synths, the drum-kit
generators from the test suite), summed into a mix, and the whole
TabForge pipeline — separation included — runs on that mix. Every error
is then measurable: per-instrument note F1 (mir_eval, onset+pitch),
octave-error rate, and LEAKAGE — notes of one instrument surfacing in
another's part, the "brass became guitar" disease.

Usage:
    .venv/bin/python scripts/eval_transcription.py            # full table
    .venv/bin/python scripts/eval_transcription.py --tracks rock_drop_d

Outputs eval_out/<track>/ work dirs and prints the summary table; the
frozen baseline lives in docs/eval.md.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

SR = 44100
RNG = np.random.default_rng(2026)

# ---------------------------------------------------------------------------
# Instrument renderers (deterministic, dependency-free)
# ---------------------------------------------------------------------------


def _hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def ks_pluck(midi: int, dur: float, damp: float = 0.995,
             drive: float = 0.0) -> np.ndarray:
    """Karplus-Strong plucked string; drive adds soft-clip distortion."""
    period = max(2, int(round(SR / _hz(midi))))
    n = int(dur * SR)
    buf = RNG.uniform(-1, 1, period)
    out = np.empty(n)
    for i in range(n):
        out[i] = buf[i % period]
        buf[i % period] = damp * 0.5 * (buf[i % period]
                                        + buf[(i + 1) % period])
    if drive > 0:
        out = np.tanh((1 + 4 * drive) * out)
    return out * np.exp(-np.arange(n) / SR / max(dur, 0.4))


def brass(midi: int, dur: float) -> np.ndarray:
    """Saw-ish partial stack, slow attack, gentle vibrato — a horn."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    vib = 1 + 0.004 * np.sin(2 * np.pi * 5.2 * t)
    y = np.zeros(n)
    for k in range(1, 9):
        y += np.sin(2 * np.pi * _hz(midi) * k * vib * t) / k
    attack = np.minimum(1.0, t / 0.06)
    release = np.minimum(1.0, (dur - t) / 0.05)
    return y * attack * np.clip(release, 0, 1)


def synth_lead(midi: int, dur: float) -> np.ndarray:
    """Two detuned saws — an unmistakable synthesizer."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    y = np.zeros(n)
    for detune in (-0.06, 0.06):
        f = _hz(midi + detune)
        for k in range(1, 12):
            y += np.sin(2 * np.pi * f * k * t) / k
    env = np.minimum(1.0, t / 0.01) * np.minimum(1.0, (dur - t) / 0.04)
    return y * np.clip(env, 0, 1) * 0.5


def _band_noise(dur: float, lo: float, hi: float, tau: float) -> np.ndarray:
    n = int(dur * SR)
    freqs = np.fft.rfftfreq(n, 1 / SR)
    spec = np.zeros(len(freqs), complex)
    sel = (freqs >= lo) & (freqs < hi)
    spec[sel] = np.exp(2j * np.pi * RNG.random(int(sel.sum())))
    y = np.fft.irfft(spec, n)
    y /= max(1e-9, np.abs(y).max())
    return y * np.exp(-np.arange(n) / SR / tau)


def _tone(f: float, dur: float, tau: float, partials=((1, 1.0),)) -> np.ndarray:
    t = np.arange(int(dur * SR)) / SR
    y = sum(a * np.sin(2 * np.pi * f * k * t) for k, a in partials)
    return y * np.exp(-t / tau)


def drum_hit(gm: int) -> np.ndarray:
    if gm == 36:
        return _tone(55, 0.3, 0.1)
    if gm == 38:
        return _band_noise(0.3, 150, 2000, 0.08)
    if gm == 42:
        return _band_noise(0.25, 5000, 9000, 0.02) * 0.6
    if gm == 46:
        return _band_noise(0.3, 5000, 9000, 0.15) * 0.6
    if gm == 49:
        return _band_noise(0.5, 4000, 10000, 0.4) * 0.7
    if gm == 51:
        return _tone(5200, 0.5, 0.5, ((1, 1.0), (1.48, 0.6), (2.1, 0.3))) * 0.5
    if gm == 41:
        return _tone(95, 0.35, 0.25, ((1, 1.0), (2, 0.4)))
    if gm == 47:
        return _tone(140, 0.3, 0.2, ((1, 1.0), (2, 0.4)))
    if gm == 50:
        return _tone(210, 0.3, 0.18, ((1, 1.0), (2, 0.4)))
    return _band_noise(0.2, 200, 2000, 0.05)


RENDERERS = {
    "guitar": lambda m, d: ks_pluck(m, d, drive=0.5),
    "bass": lambda m, d: ks_pluck(m, d, damp=0.997),
    "brass": brass,
    "synth": synth_lead,
}

# ---------------------------------------------------------------------------
# The pieces. Each is {instrument: [(midi, start_beats, dur_beats), ...]}
# with drums as GM numbers. Where the transcriber SHOULD put each
# instrument: guitar->guitar, bass->bass, drums->drums, brass/synth->other.
# ---------------------------------------------------------------------------

HOME = {"guitar": ("guitar", "guitar_lead", "guitar_rhythm"),
        "bass": ("bass",),
        "brass": ("other", "piano", "piano_left"),
        "synth": ("other", "piano", "piano_left"),
        "drums": ("drums",)}


def _beat_rock(bars: int):
    out = []
    for b in range(bars * 4):
        out.append((36, b, 0.5)) if b % 2 == 0 else out.append((38, b, 0.5))
        out.append((42, b, 0.25))
        out.append((42, b + 0.5, 0.25))
    out.append((49, 0, 1.0))
    return out


def _power(root: int, start: float, dur: float):
    return [(root, start, dur), (root + 7, start, dur), (root + 12, start, dur)]


def make_tracks() -> dict:
    tracks = {}

    # 1. rock in drop D: power chords, eighth bass, straight beat
    g, b = [], []
    prog = [38, 41, 43, 45]                 # D2 F2 G2 A2
    for bar, root in enumerate(prog * 2):
        g += _power(root, bar * 4, 3.5)
        for e in range(8):
            b.append((root - 12, bar * 4 + e * 0.5, 0.45))
    tracks["rock_drop_d"] = {"bpm": 120, "guitar": g, "bass": b,
                             "drums": _beat_rock(8)}

    # 2. ballad with a brass melody over guitar arpeggio
    g, br, b = [], [], []
    chords = [(45, 52, 57, 61), (43, 50, 55, 59),
              (41, 48, 53, 57), (43, 50, 55, 59)]
    melody = [(69, 0, 3), (72, 3, 1), (71, 4, 2.5), (67, 6.5, 1.5),
              (69, 8, 3), (74, 11, 1), (72, 12, 4)]
    for bar, ch in enumerate(chords * 2):
        for i, p in enumerate(ch):
            g.append((p, bar * 4 + i * 0.5, 1.5))
            g.append((p, bar * 4 + 2 + i * 0.5, 1.5))
        b.append((ch[0] - 12, bar * 4, 3.5))
    for p, s, d in melody:
        br.append((p, s, d))
        br.append((p, s + 16, d))
    tracks["brass_ballad"] = {"bpm": 90, "guitar": g, "bass": b, "brass": br,
                              "drums": [(42, i, 0.25) for i in range(32)]}

    # 3. synth pop: lead synth, bass, four-on-the-floor
    sy, b = [], []
    lead = [64, 67, 69, 71, 69, 67, 64, 62]
    for bar in range(8):
        sy.append((lead[bar % len(lead)] + 12, bar * 4, 2.0))
        sy.append((lead[(bar + 3) % len(lead)] + 12, bar * 4 + 2, 1.5))
        for beat in range(4):
            b.append((40 if bar % 2 else 45, bar * 4 + beat, 0.9))
    dr = [(36, i, 0.5) for i in range(32)] \
        + [(46, i + 0.5, 0.25) for i in range(32)]
    tracks["synth_pop"] = {"bpm": 126, "synth": sy, "bass": b, "drums": dr}

    # 4. metal drop C: chugs and a floor-tom-heavy beat
    g, b = [], []
    for bar in range(8):
        for e in range(6):
            g += _power(36, bar * 4 + e * 0.5, 0.4)
        g += _power(39 if bar % 2 else 43, bar * 4 + 3, 0.9)
        for e in range(8):
            b.append((24, bar * 4 + e * 0.5, 0.4))
    dr = []
    for bar in range(8):
        for e in range(8):
            dr.append((36, bar * 4 + e * 0.5, 0.25))
        dr.append((38, bar * 4 + 1, 0.4))
        dr.append((38, bar * 4 + 3, 0.4))
        dr.append((41, bar * 4 + 3.5, 0.4))
    tracks["metal_drop_c"] = {"bpm": 140, "guitar": g, "bass": b, "drums": dr}

    # 5. funk: syncopated bass, high guitar stabs, a ride pattern
    g, b = [], []
    for bar in range(8):
        for s in (0, 0.75, 1.5, 2.25, 3.0, 3.5):
            b.append((33 + (5 if s in (1.5, 3.5) else 0), bar * 4 + s, 0.4))
        for s in (1.0, 3.25):
            for p in (64, 69, 74):
                g.append((p, bar * 4 + s, 0.3))
    dr = [(51, i * 0.5, 0.3) for i in range(64)] \
        + [(36, i * 2, 0.4) for i in range(16)] \
        + [(38, i * 2 + 1, 0.4) for i in range(16)]
    tracks["funk"] = {"bpm": 104, "guitar": g, "bass": b, "drums": dr}

    return tracks


def render_track(spec: dict, out_dir: Path) -> dict:
    """Renders the mix and returns the ground truth in SECONDS."""
    import soundfile as sf

    bpm = spec["bpm"]
    beat = 60.0 / bpm
    length = 0.0
    truth: dict[str, list] = {}
    for inst, notes in spec.items():
        if inst == "bpm":
            continue
        truth[inst] = [(int(p), s * beat, max(d * beat, 0.1))
                       for p, s, d in notes]
        length = max(length, max(s + d for _, s, d in truth[inst]) + 1.0)

    mix = np.zeros(int(length * SR))
    for inst, notes in truth.items():
        stem = np.zeros_like(mix)
        for p, s, d in notes:
            wave = (drum_hit(p) if inst == "drums"
                    else RENDERERS[inst](p, d))
            i = int(s * SR)
            wave = wave[: len(stem) - i]
            stem[i: i + len(wave)] += wave
        peak = np.abs(stem).max() or 1.0
        gain = {"bass": 0.9, "drums": 0.8}.get(inst, 0.7)
        mix += stem / peak * gain
    mix /= max(1.0, np.abs(mix).max() * 1.05)
    out_dir.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_dir / "mix.wav"), mix, SR)
    return truth


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

ONSET_TOL = 0.08


def _match_stats(ref: list, est: list) -> tuple[float, float, float]:
    import mir_eval

    if not ref or not est:
        return 0.0, 0.0, 0.0
    ref_i = np.array([[s, s + d] for _, s, d in ref])
    ref_p = np.array([_hz(p) for p, _, _ in ref])
    est_i = np.array([[s, s + d] for _, s, d in est])
    est_p = np.array([_hz(p) for p, _, _ in est])
    p, r, f, _ = mir_eval.transcription.precision_recall_f1_overlap(
        ref_i, ref_p, est_i, est_p,
        onset_tolerance=ONSET_TOL, offset_ratio=None)
    return p, r, f


def _octave_error_rate(ref: list, est: list) -> float:
    """Of the reference notes NOT matched exactly, how many match at
    ±1 octave? (the octave-jump disease, worst in the low register)"""
    misses = 0
    octaves = 0
    for p, s, _ in ref:
        exact = any(abs(es - s) < ONSET_TOL and ep == p
                    for ep, es, _ in est)
        if exact:
            continue
        misses += 1
        if any(abs(es - s) < ONSET_TOL and abs(ep - p) == 12
               for ep, es, _ in est):
            octaves += 1
    return octaves / misses if misses else 0.0


def evaluate_track(name: str, spec: dict, work: Path) -> list[dict]:
    from tabforge.pipeline import PipelineOptions, run_pipeline

    truth = render_track(spec, work)
    opts = PipelineOptions(stems=("guitar", "bass", "piano", "vocals",
                                  "other", "drums"), subdivision=2)
    results = run_pipeline(work / "mix.wav", work / "out", opts)

    # transcribed notes per part, from the saved parts.json + drums
    import json
    parts_file = work / "out" / "parts.json"
    est_by_part: dict[str, list] = {}
    if parts_file.exists():
        state = json.loads(parts_file.read_text())
        for part, p in state.items():
            est_by_part[part] = [(n["pitch"], n["start"], n["duration"])
                                 for n in p["notes"]]
    for r in results:                          # drums have no parts.json
        if r.stem == "drums":
            from tabforge.audio.drums import transcribe_drums
            drums_wav = next((work / "out" / "stems").rglob("drums.wav"), None)
            if drums_wav:
                est_by_part["drums"] = [(n.pitch, n.start, n.duration)
                                        for n in transcribe_drums(drums_wav)]

    rows = []
    for inst, ref in truth.items():
        home = [est_by_part.get(h, []) for h in HOME[inst]]
        est_home = [n for part in home for n in part]
        foreign = [n for part_name, notes in est_by_part.items()
                   if part_name not in HOME[inst] for n in notes]
        p, r, f = _match_stats(ref, est_home)
        # leakage: reference notes of THIS instrument found in parts
        # where they do not belong
        leaked = sum(
            1 for pp, ss, _ in ref
            if any(abs(es - ss) < ONSET_TOL and ep % 12 == pp % 12
                   for ep, es, _ in foreign))
        rows.append({
            "track": name, "instrument": inst,
            "ref_notes": len(ref), "est_notes": len(est_home),
            "precision": p, "recall": r, "f1": f,
            "octave_err": _octave_error_rate(ref, est_home),
            "leak_rate": leaked / len(ref) if ref else 0.0,
        })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tracks", nargs="*", default=None)
    ap.add_argument("--out", default=str(ROOT / "eval_out"))
    args = ap.parse_args()

    tracks = make_tracks()
    picked = args.tracks or sorted(tracks)
    all_rows = []
    for name in picked:
        print(f"=== {name} ===", flush=True)
        rows = evaluate_track(name, tracks[name], Path(args.out) / name)
        all_rows += rows

    header = (f"{'track':14s} {'inst':7s} {'ref':>4s} {'est':>4s} "
              f"{'P':>5s} {'R':>5s} {'F1':>5s} {'oct':>5s} {'leak':>5s}")
    print("\n" + header)
    print("-" * len(header))
    for r in all_rows:
        print(f"{r['track']:14s} {r['instrument']:7s} "
              f"{r['ref_notes']:4d} {r['est_notes']:4d} "
              f"{r['precision']:5.2f} {r['recall']:5.2f} {r['f1']:5.2f} "
              f"{r['octave_err']:5.2f} {r['leak_rate']:5.2f}")
    by_inst: dict[str, list] = {}
    for r in all_rows:
        by_inst.setdefault(r["instrument"], []).append(r)
    print("-" * len(header))
    for inst, rows in sorted(by_inst.items()):
        f1 = np.mean([r["f1"] for r in rows])
        oct_ = np.mean([r["octave_err"] for r in rows])
        leak = np.mean([r["leak_rate"] for r in rows])
        print(f"{'MEAN':14s} {inst:7s} {'':4s} {'':4s} "
              f"{'':5s} {'':5s} {f1:5.2f} {oct_:5.2f} {leak:5.2f}")


if __name__ == "__main__":
    main()
