"""Standalone YourMT3+ runner — executed INSIDE the MT3 venv, never
imported by tabforge itself.

    <mt3_dir>/venv-mt3/bin/python _mt3_run.py <ymt3space> <mix> <out.mid>

The environment recipe (venv, deps, checkpoint) lives in
scripts/mt3_experiment/README.md; TABFORGE_MT3_DIR points at the
directory that holds ymt3space/ and venv-mt3/.
"""
import shutil
import sys
from pathlib import Path

SPACE = Path(sys.argv[1]).resolve()
MIX = Path(sys.argv[2]).resolve()
OUT = Path(sys.argv[3]).resolve()

sys.path.insert(0, str(SPACE / "amt" / "src"))
sys.path.insert(0, str(SPACE))

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
    midifile = transcribe(model, {"filepath": str(MIX),
                                  "track_name": MIX.stem})
    shutil.copy(midifile, OUT)
    print(f"ok {OUT}", flush=True)


if __name__ == "__main__":
    main()
