# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the TabForge desktop app.
# Build:  pyinstaller TabForge.spec   ->  dist/TabForge.app
#
# The Demucs model weights are NOT bundled: they download into
# ~/.cache/torch/hub/checkpoints on the first run, same as the dev setup.

from PyInstaller.utils.hooks import collect_all, collect_data_files

datas = [("frontend", "frontend"),
         # the MT3 arbiter and MuScriptor run these as SUBPROCESS
         # scripts in external venvs — they must exist on disk, not
         # inside the PYZ
         ("src/tabforge/audio/_mt3_run.py", "tabforge/audio"),
         ("src/tabforge/audio/_muscriptor_run.py", "tabforge/audio")]
binaries = []
hiddenimports = []

# Packages that carry data files or dynamically-imported submodules the
# static analysis misses: demucs (remote model registry yamls), basic_pitch
# (the bundled CoreML model), librosa (registry data), resampy (precomputed
# filters), torchaudio backends, sklearn internals used by librosa.
# panns_inference (+ its torchlibrosa dep) is the instrument tagger:
# without it the desktop app loses "sounds like" AND the MT3 arbiter's
# self-tag guard silently degrades to benefit-of-doubt
for pkg in ("demucs", "basic_pitch", "librosa", "resampy",
            "torchaudio", "coremltools", "panns_inference",
            "torchlibrosa"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

datas += collect_data_files("sklearn")
hiddenimports += ["sklearn.utils._typedefs", "sklearn.neighbors._partition_nodes",
                  "uvicorn.logging", "uvicorn.loops.auto",
                  "uvicorn.protocols.http.auto", "uvicorn.protocols.websockets.auto",
                  "uvicorn.lifespan.on"]

a = Analysis(
    ["scripts/desktop_launcher.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "tkinter", "PyQt5", "PySide6"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TabForge",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="TabForge",
)

app = BUNDLE(
    coll,
    name="TabForge.app",
    icon=None,
    bundle_identifier="com.tabforge.app",
    info_plist={
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "12.0",
    },
)
