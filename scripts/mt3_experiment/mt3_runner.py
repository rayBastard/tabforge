"""Task 51 experiment: YourMT3+ on the eval-stand mixes.

Loads the YPTF.MoE+Multi (noPS) checkpoint on CPU, transcribes each
stand mix WHOLE (no separation), maps the predicted notes' MIDI
programs to our instrument classes, and scores against the same ground
truth as scripts/eval_transcription.py.

Run inside the mt3 venv:
    venv-mt3/bin/python mt3_runner.py <tabforge_root> <mixes...>
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPACE = HERE / "ymt3space"
sys.path.insert(0, str(SPACE / "amt" / "src"))
sys.path.insert(0, str(SPACE))

TABFORGE = Path(sys.argv[1])
sys.path.insert(0, str(TABFORGE / "scripts"))
sys.path.insert(0, str(TABFORGE / "src"))

import numpy as np  # noqa: E402

# torchaudio>=2.9 delegates load() to torchcodec, whose dylib needs a
# system ffmpeg — bypass it with a soundfile loader before their code
# ever touches it
import soundfile as _sf  # noqa: E402
import torch as _torch  # noqa: E402
import torchaudio as _ta  # noqa: E402


def _sf_load(uri=None, *args, **kwargs):
    data, sr = _sf.read(str(uri), always_2d=True)
    return _torch.tensor(data.T, dtype=_torch.float32), sr


_ta.load = _sf_load

from model_helper import load_model_checkpoint, transcribe  # noqa: E402

# GM program -> our stand instrument class
def inst_of(note) -> str:
    if note.is_drum:
        return "drums"
    p = note.program
    if p <= 7:
        return "piano"          # keys land in "other"-home for scoring
    if 24 <= p <= 31:
        return "guitar"
    if 32 <= p <= 39:
        return "bass"
    if 56 <= p <= 63 or 64 <= p <= 71:
        return "brass"          # brass + reeds
    if 80 <= p <= 95:
        return "synth"
    return "other"


def main() -> None:
    ckpt = ("mc13_256_g4_all_v7_mt3f_sqr_rms_moe_wf4_n8k2_silu_rope_rp"
            "_b36_nops@last.ckpt")
    args = [ckpt, '-p', '2024', '-tk', 'mc13_full_plus_256',
            '-dec', 'multi-t5', '-nl', '26', '-enc', 'perceiver-tf',
            '-ac', 'spec', '-hop', '300', '-atc', '1', '-pr', '32',
            '-ln', 'rms', '-sqr', '1', '-ff', 'moe',
            '-wf', '4', '-nmoe', '8', '-kmoe', '2', '-act', 'silu',
            '-epe', 'rope', '-rp', '1']
    import os
    os.chdir(SPACE)                      # their code resolves amt/logs/
    model = load_model_checkpoint(args=args, device="cpu")
    print("model loaded", flush=True)

    out = {}
    for mix in sys.argv[2:]:
        mix = Path(mix)
        t0 = time.time()
        midifile = transcribe(model, {"filepath": str(mix),
                                      "track_name": mix.parent.name})
        dt = time.time() - t0
        # transcribe() writes a midi; but we want the notes: re-run the
        # core to grab pred_notes — simpler: parse its midi output
        print(f"{mix.parent.name}: transcribed in {dt:.0f}s -> {midifile}",
              flush=True)
        out[mix.parent.name] = midifile
    (HERE / "mt3_results.json").write_text(json.dumps(out))


if __name__ == "__main__":
    main()
