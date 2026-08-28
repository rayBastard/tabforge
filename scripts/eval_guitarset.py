"""
The GuitarSet ruler (task 65): the first external measurement of the
Viterbi fingering itself.

GuitarSet (Xi et al., Zenodo 3371780) ships hexaphonic truth: every
note annotated PER STRING as a real guitarist actually played it. Two
independent metrics:

1. --strings: STRING ASSIGNMENT accuracy — feed the TRUTH notes into
   `assign_tab` and count how often our Viterbi picks the same string
   as the human. Pure math, no audio, isolates the fingering engine
   from transcription errors. Also prints the confusion histogram
   (which strings we confuse, open vs fretted, by register) — the
   spec for any future cost tuning, per the plan: no blind fixes.

2. --transcribe: note transcription F1 on the mono-mic audio through
   a chosen backend (bp | muscriptor | gaps), mir_eval strict —
   comparable to the literature.

    .venv/bin/python scripts/eval_guitarset.py --strings
    .venv/bin/python scripts/eval_guitarset.py --transcribe bp --limit 30

Data layout (downloaded from Zenodo record 3371780):
    <root>/annotation/*.jams
    <root>/audio/*_mic.wav
Default root: the session scratchpad's guitarset/ (see --root).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

STANDARD = (40, 45, 50, 55, 59, 64)      # matches GuitarSet sources 0..5


def load_truth(jams_path: Path):
    """[(pitch, start, dur, string_idx)] from the hexaphonic truth."""
    data = json.loads(jams_path.read_text())
    notes = []
    for ann in data["annotations"]:
        if ann["namespace"] != "note_midi":
            continue
        string = int(ann["annotation_metadata"]["data_source"])
        for obs in ann["data"]:
            notes.append((int(round(obs["value"])), float(obs["time"]),
                          max(float(obs["duration"]), 0.05), string))
    notes.sort(key=lambda n: (n[1], n[0]))
    return notes


def eval_strings(root: Path, limit: int | None = None) -> None:
    from tabforge.core import TabConfig, TUNINGS
    from tabforge.core.fretboard import NoteEvent, assign_tab

    cfg = TabConfig(tuning=TUNINGS["standard"])
    files = sorted((root / "annotation").glob("*.jams"))
    if limit:
        files = files[:limit]
    total = agree = 0
    confusion: Counter = Counter()
    open_total = open_agree = 0
    fretted_total = fretted_agree = 0
    by_style: dict[str, list[int]] = {}
    for f in files:
        truth = load_truth(f)
        if not truth:
            continue
        events = [NoteEvent(p, s, d) for p, s, d, _ in truth]
        shapes = assign_tab(events, cfg)
        assigned = {}
        for shape in shapes:
            for pl in shape.placements:
                assigned[id(pl.note)] = pl.string
        style = f.stem.split("_")[1][:2]
        st = by_style.setdefault(style, [0, 0])
        for ev, (p, s, d, true_string) in zip(events, truth):
            ours = assigned.get(id(ev))
            if ours is None:
                continue
            total += 1
            st[1] += 1
            is_open = (p - STANDARD[true_string]) == 0
            if is_open:
                open_total += 1
            else:
                fretted_total += 1
            if ours == true_string:
                agree += 1
                st[0] += 1
                if is_open:
                    open_agree += 1
                else:
                    fretted_agree += 1
            else:
                confusion[(true_string, ours)] += 1

    print(f"STRING ASSIGNMENT: {agree}/{total} = {agree/total:.3f}")
    print(f"  open strings:   {open_agree}/{open_total} = "
          f"{open_agree/max(open_total,1):.3f}")
    print(f"  fretted notes:  {fretted_agree}/{fretted_total} = "
          f"{fretted_agree/max(fretted_total,1):.3f}")
    print("  by style:", {k: f"{a/max(n,1):.3f}"
                          for k, (a, n) in sorted(by_style.items())})
    print("  top confusions (true->ours):")
    names = ("E", "A", "D", "G", "B", "e")
    for (t, o), c in confusion.most_common(8):
        print(f"    {names[t]} -> {names[o]}: {c}")


def eval_transcription(root: Path, backend: str,
                       limit: int | None = None) -> None:
    import numpy as np

    sys.path.insert(0, str(ROOT / "scripts"))
    from eval_transcription import _hz
    import mir_eval

    files = sorted((root / "annotation").glob("*.jams"))
    if limit:
        files = files[:limit]

    model = None
    if backend == "gaps":
        from huggingface_hub import hf_hub_download
        from hf_midi_transcription import MidiTranscriptionModel
        ckpt = hf_hub_download("xavriley/midi-transcription-models",
                               "guitar-gaps.pth")
        model = MidiTranscriptionModel(instrument="guitar",
                                       checkpoint_path=ckpt)

    scores = []
    for f in files:
        wav = root / "audio" / (f.stem + "_mic.wav")
        if not wav.exists():
            continue
        truth = load_truth(f)
        ref_i = np.array([[s, s + d] for _, s, d, _ in truth])
        ref_p = np.array([_hz(p) for p, _, _, _ in truth])
        if backend == "bp":
            from tabforge.audio import transcribe as T
            notes = T.cleanup(T.transcribe_stem(
                wav, **T.PRESETS.get("guitar", {})))
            est = [(n.pitch, n.start, n.duration) for n in notes]
        elif backend == "muscriptor":
            import subprocess, tempfile
            from tabforge.audio.arbiter import mt3_card_notes
            from tabforge.audio.muscriptor import (find_muscriptor,
                                                   variant)
            with tempfile.TemporaryDirectory() as td:
                out = Path(td) / "m.mid"
                runner = (ROOT / "src" / "tabforge" / "audio"
                          / "_muscriptor_run.py")
                subprocess.run([str(find_muscriptor()), str(runner),
                                str(wav), str(out), variant()],
                               check=True, capture_output=True)
                got = mt3_card_notes(out, "guitar") or []
            est = [(n.pitch, n.start, n.duration) for n in got]
        elif backend == "gaps":
            import tempfile
            import pretty_midi
            with tempfile.TemporaryDirectory() as td:
                out = Path(td) / "g.mid"
                model.transcribe(str(wav), str(out))
                pm = pretty_midi.PrettyMIDI(str(out))
                est = [(n.pitch, n.start, max(n.end - n.start, 0.05))
                       for t in pm.instruments for n in t.notes]
        else:
            raise SystemExit(f"unknown backend {backend}")
        if not est:
            scores.append(0.0)
            continue
        est_i = np.array([[s, s + d] for _, s, d in est])
        est_p = np.array([_hz(p) for p, _, _ in est])
        _, _, f1, _ = mir_eval.transcription.precision_recall_f1_overlap(
            ref_i, ref_p, est_i, est_p, onset_tolerance=0.05,
            offset_ratio=None)
        scores.append(f1)
    print(f"TRANSCRIPTION [{backend}] on {len(scores)} excerpts: "
          f"mean F1 {np.mean(scores):.3f} median {np.median(scores):.3f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    default_root = Path(
        "/private/tmp/claude-501/-Users-rc-Desktop-tabforge/"
        "d62a89ba-78b3-4d8f-b883-d826ab37e798/scratchpad/guitarset")
    ap.add_argument("--root", type=Path, default=default_root)
    ap.add_argument("--strings", action="store_true")
    ap.add_argument("--transcribe", default=None,
                    choices=("bp", "muscriptor", "gaps"))
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    if args.strings:
        eval_strings(args.root, args.limit)
    if args.transcribe:
        eval_transcription(args.root, args.transcribe, args.limit)


if __name__ == "__main__":
    main()
