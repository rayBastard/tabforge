"""
MuScriptor note source (task 57).

MuScriptor-small (Kyutai/Mirelo, 2026) transcribes the WHOLE MIX into
multi-instrument MIDI and, unlike MT3, is NOT blind on heavy Suno
material — golden numbers against our stem paths:

    bass    0.43 -> 0.62  (Loken 0.90: P 0.89 / R 0.91)
    guitar  0.29 -> 0.41  (Loken 0.51 at P 0.90)

at ~0.4x realtime on this machine. Piano stays on MT3 (+0.02 only),
drums on our classifier, vocals on the mono path (MuScriptor hears
6-65 notes of Suno's semi-recitative — blind there).

Licensing: the CODE is MIT, but the WEIGHTS are CC BY-NC 4.0 and
gated on Hugging Face — so this backend is never bundled or
auto-installed. It activates only when the user has built the venv
themselves (see _muscriptor_run.py for the recipe); without it every
routed instrument silently falls back to its stem path. Installing
into tabforge's own venv is deliberately NOT offered: MuScriptor's
pins would downgrade torchaudio under demucs.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_DEFAULT_DIRS = ("~/muscriptor", "~/.tabforge/muscriptor")
TIMEOUT_S = 3600


def variant() -> str:
    """Which MuScriptor to run. medium beats small everywhere it was
    measured (solo bass 0.68->0.81, solo guitar 0.41->0.46, golden
    piano 0.60->0.65) at ~2.5x the time (~0.9x realtime on Metal), so
    it becomes the default AS SOON as its gated weights are on this
    machine; TABFORGE_MUSCRIPTOR_MODEL overrides either way."""
    env = os.environ.get("TABFORGE_MUSCRIPTOR_MODEL")
    if env:
        return env
    hub = (Path.home() / ".cache" / "huggingface" / "hub"
           / "models--MuScriptor--muscriptor-medium")
    return "medium" if hub.is_dir() else "small"


def find_muscriptor() -> Path | None:
    """The venv python of a MuScriptor install, if one is configured."""
    env = os.environ.get("TABFORGE_MUSCRIPTOR_DIR")
    roots = [env] if env else list(_DEFAULT_DIRS)
    for root in roots:
        python = Path(root).expanduser() / "venv" / "bin" / "python"
        if python.exists():
            return python
    return None


def run_muscriptor(mix: Path, work_dir: Path,
                   progress=lambda *_: None) -> Path | None:
    """Transcribe the mix (cached as work_dir/muscriptor.mid)."""
    out = work_dir / "muscriptor.mid"
    if out.exists():
        return out
    python = find_muscriptor()
    if python is None:
        return None
    runner = Path(__file__).with_name("_muscriptor_run.py")
    if not runner.exists():           # frozen app: shipped as a data file
        import sys
        runner = (Path(getattr(sys, "_MEIPASS", ""))
                  / "tabforge" / "audio" / "_muscriptor_run.py")
    if not runner.exists():
        return None
    progress("transcribe", "MuScriptor: transcribing the whole mix "
                           "(~0.4x realtime)")
    env = {k: v for k, v in os.environ.items()
           if k not in ("DYLD_LIBRARY_PATH", "LD_LIBRARY_PATH",
                        "PYTHONPATH", "PYTHONHOME", "_MEIPASS2")}
    var = variant()
    try:
        subprocess.run([str(python), str(runner), str(mix), str(out),
                        var],
                       check=True, capture_output=True,
                       timeout=TIMEOUT_S, env=env)
    except (subprocess.SubprocessError, OSError):
        progress("transcribe", "MuScriptor unavailable — stem "
                               "transcription keeps the job")
        return None
    if out.exists():
        out.with_suffix(".variant").write_text(var)
        return out
    return None
