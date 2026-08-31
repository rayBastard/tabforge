"""Bend-note recall ruler (deferred block, 2026-08-31).

The user's observation: passages with bends vanish from the
transcription. Quantified here on GuitarSet solos, whose hexaphonic
pitch contours mark exactly which truth notes are pitch-inflected
(bends/slides/deep vibrato: >= quartertone contour deviation from the
notated pitch). The metric is RECALL of bent vs plain truth notes per
engine — the gap IS the complaint, measured. Matching: onset within
60 ms, pitch within 1 semitone (a bent note's landing pitch wanders).

    python scripts/eval_bends.py --engine muscriptor|gaps|bp [--limit N]

Needs the GuitarSet audio at <root>/audio/*_mic.wav (see
eval_guitarset.py for the download).
"""
import argparse
import bisect
import math
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import jams


def truth_with_bends(jam_path: Path):
    """[(start, dur, midi, bent), ...] from a solo jams file."""
    j = jams.load(str(jam_path), validate=False)
    contours = [a for a in j.annotations if a.namespace == "pitch_contour"]
    notes = [a for a in j.annotations if a.namespace == "note_midi"]
    out = []
    for na, ca in zip(notes, contours):
        cav = sorted((o.time, o.value["frequency"]) for o in ca.data
                     if o.value.get("frequency", 0) > 0)
        times = [t for t, _ in cav]
        for o in na.data:
            lo = bisect.bisect_left(times, o.time)
            hi = bisect.bisect_right(times, o.time + o.duration)
            bent = False
            if hi > lo:
                devs = [abs(12 * math.log2(fr / 440) + 69 - o.value)
                        for _t, fr in cav[lo:hi]]
                bent = bool(devs) and max(devs) >= 0.5
            out.append((o.time, o.duration, o.value, bent))
    return out


def transcribe(engine: str, wav: Path):
    if engine == "bp":
        from tabforge.audio import transcribe as T
        notes = T.cleanup(T.transcribe_stem(
            wav, **T.PRESETS.get("guitar", {})))
        return [(n.start, n.pitch) for n in notes]
    if engine == "muscriptor":
        import subprocess

        from tabforge.audio.arbiter import mt3_card_notes
        from tabforge.audio.muscriptor import find_muscriptor, variant
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "m.mid"
            runner = ROOT / "src/tabforge/audio/_muscriptor_run.py"
            subprocess.run([str(find_muscriptor()), str(runner),
                            str(wav), str(out), variant()],
                           check=True, capture_output=True)
            got = mt3_card_notes(out, "guitar") or []
        return [(n.start, n.pitch) for n in got]
    if engine == "gaps":
        import pretty_midi
        from hf_midi_transcription import MidiTranscriptionModel
        from huggingface_hub import hf_hub_download
        ckpt = hf_hub_download("xavriley/midi-transcription-models",
                               "guitar-gaps.pth")
        model = MidiTranscriptionModel(instrument="guitar",
                                       checkpoint_path=ckpt)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "g.mid"
            model.transcribe(str(wav), str(out))
            pm = pretty_midi.PrettyMIDI(str(out))
        return [(n.start, n.pitch)
                for t in pm.instruments for n in t.notes]
    raise SystemExit(f"unknown engine {engine}")


def main() -> None:
    ap = argparse.ArgumentParser()
    default_root = Path(
        "/private/tmp/claude-501/-Users-rc-Desktop-tabforge/"
        "d62a89ba-78b3-4d8f-b883-d826ab37e798/scratchpad/guitarset")
    ap.add_argument("--root", type=Path, default=default_root)
    ap.add_argument("--engine", default="muscriptor",
                    choices=("bp", "muscriptor", "gaps"))
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()

    files = sorted((args.root / "annotation").glob("*_solo.jams"))
    hit_b = tot_b = hit_p = tot_p = 0
    used = 0
    for f in files:
        wav = args.root / "audio" / (f.stem + "_mic.wav")
        if not wav.exists():
            continue
        if used >= args.limit:
            break
        used += 1
        truth = truth_with_bends(f)
        est = sorted(transcribe(args.engine, wav))
        est_t = [t for t, _ in est]
        for t0, _d, midi, bent in truth:
            i = bisect.bisect_left(est_t, t0 - 0.06)
            ok = any(abs(est_t[k] - t0) <= 0.06
                     and abs(est[k][1] - midi) <= 1.0
                     for k in range(i, len(est))
                     if est_t[k] <= t0 + 0.06)
            if bent:
                tot_b += 1
                hit_b += ok
            else:
                tot_p += 1
                hit_p += ok
    rb = hit_b / max(1, tot_b)
    rp = hit_p / max(1, tot_p)
    print(f"[{args.engine}] {used} solos: plain recall {rp:.3f} "
          f"({hit_p}/{tot_p})  BENT recall {rb:.3f} ({hit_b}/{tot_b})  "
          f"gap {rp - rb:+.3f}")


if __name__ == "__main__":
    main()
