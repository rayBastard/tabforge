"""Score YourMT3+ MIDI outputs against the stand's ground truth."""
import sys
from pathlib import Path

TABFORGE = Path(sys.argv[1])
sys.path.insert(0, str(TABFORGE / "scripts"))
sys.path.insert(0, str(TABFORGE / "src"))

import numpy as np  # noqa: E402
import pretty_midi  # noqa: E402
from eval_transcription import (_match_stats, _octave_error_rate,  # noqa: E402
                                make_tracks)

ONSET_TOL = 0.08


def inst_of(program: int, is_drum: bool) -> str:
    if is_drum:
        return "drums"
    if program <= 7:
        return "piano"
    if 24 <= program <= 31:
        return "guitar"
    if 32 <= program <= 39:
        return "bass"
    if 56 <= program <= 71:
        return "brass"
    if 80 <= program <= 95:
        return "synth"
    return "other"


# who counts as "home" for each stand instrument, in MT3 vocabulary:
# keys-ish predictions (piano/other) host brass and synth too, mirroring
# the pipeline's HOME map
HOME = {"guitar": ("guitar",), "bass": ("bass",),
        "brass": ("brass", "piano", "other"),
        "synth": ("synth", "piano", "other"),
        "drums": ("drums",)}


def main() -> None:
    tracks = make_tracks()
    rows = []
    for midi_path in sys.argv[2:]:
        midi_path = Path(midi_path)
        name = midi_path.stem
        spec = tracks[name]
        beat = 60.0 / spec["bpm"]
        truth = {inst: [(int(p), s * beat, max(d * beat, 0.1))
                        for p, s, d in notes]
                 for inst, notes in spec.items() if inst != "bpm"}

        pm = pretty_midi.PrettyMIDI(str(midi_path))
        est_by_inst: dict[str, list] = {}
        for tr in pm.instruments:
            klass = inst_of(tr.program, tr.is_drum)
            for n in tr.notes:
                est_by_inst.setdefault(klass, []).append(
                    (n.pitch, n.start, max(n.end - n.start, 0.05)))

        for inst, ref in truth.items():
            est_home = [n for k in HOME[inst]
                        for n in est_by_inst.get(k, [])]
            foreign = [n for k, notes in est_by_inst.items()
                       if k not in HOME[inst] for n in notes]
            p, r, f = _match_stats(ref, est_home)
            leaked = sum(
                1 for pp, ss, _ in ref
                if any(abs(es - ss) < ONSET_TOL and ep % 12 == pp % 12
                       for ep, es, _ in foreign))
            rows.append((name, inst, len(ref), len(est_home), p, r, f,
                         _octave_error_rate(ref, est_home),
                         leaked / len(ref) if ref else 0.0))

    print(f"{'track':14s} {'inst':7s} {'ref':>4s} {'est':>4s} "
          f"{'P':>5s} {'R':>5s} {'F1':>5s} {'oct':>5s} {'leak':>5s}")
    for r in rows:
        print(f"{r[0]:14s} {r[1]:7s} {r[2]:4d} {r[3]:4d} "
              f"{r[4]:5.2f} {r[5]:5.2f} {r[6]:5.2f} {r[7]:5.2f} {r[8]:5.2f}")
    by = {}
    for r in rows:
        by.setdefault(r[1], []).append(r)
    print("-" * 60)
    for inst, rs in sorted(by.items()):
        print(f"{'MEAN':14s} {inst:7s} {'':10s}"
              f"{'':12s}{np.mean([x[6] for x in rs]):5.2f} "
              f"{np.mean([x[7] for x in rs]):5.2f} "
              f"{np.mean([x[8] for x in rs]):5.2f}")


if __name__ == "__main__":
    main()
