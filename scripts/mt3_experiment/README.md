# Task 51: the YourMT3+ experiment

End-to-end multi-instrument transcription (mix in, notes WITH
instrument labels out) against our separation+BasicPitch pipeline.

Setup (one-time, ~3 GB):

    git clone https://huggingface.co/spaces/mimbres/YourMT3 <dir>/ymt3space
    python3.11 -m venv <dir>/venv-mt3
    <dir>/venv-mt3/bin/pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
    <dir>/venv-mt3/bin/pip install "lightning>=2.2.1" "transformers==4.45.1" \
        einops mido librosa "numpy==1.26.4" deprecated mir_eval soundfile \
        pretty_midi python-dotenv wandb
    # the YPTF.MoE+Multi (noPS) checkpoint, 544 MB:
    curl -L "https://huggingface.co/spaces/mimbres/YourMT3/resolve/main/amt/logs/2024/mc13_256_g4_all_v7_mt3f_sqr_rms_moe_wf4_n8k2_silu_rope_rp_b36_nops/checkpoints/last.ckpt" \
        -o <dir>/ymt3space/amt/logs/2024/.../checkpoints/last.ckpt

Run (mt3_runner.py monkeypatches torchaudio.load with soundfile — the
new torchaudio delegates to torchcodec, which wants a system ffmpeg):

    <dir>/venv-mt3/bin/python mt3_runner.py <tabforge_root> <mix.wav ...>
    .venv/bin/python score_mt3.py <tabforge_root> <midi ...>

Findings (2026-08-26): see docs/eval.md, task 51.
