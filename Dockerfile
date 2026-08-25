# TabForge server image (phase 5): ml + export + server extras, no desktop.
#
# Model weights are NOT baked in: demucs and basic-pitch download into
# ~/.cache on first use — mount a volume there (see docker-compose.yml)
# so they survive container recreation.

FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU-only torch/torchaudio first: the default index would pull multi-GB
# CUDA wheels on amd64 that a CPU server never uses.
# torchaudio >= 2.9 delegates wav writing to torchcodec (which uses the
# ffmpeg libs installed above) — demucs fails saving stems without it.
# All three MUST come from the CPU index: the PyPI torchcodec wheel is
# built against CUDA torch and dies loading libnvrtc.
RUN pip install --no-cache-dir torch torchaudio torchcodec \
    --index-url https://download.pytorch.org/whl/cpu

COPY pyproject.toml README.md ./
COPY src ./src
COPY frontend ./frontend
# demucs is pinned to 4.0.x in the image: 4.1 depends on sphn, which ships
# no linux/arm64 wheels and would need a Rust toolchain to build. We drive
# demucs through its CLI, which is identical in both versions.
# The editable install keeps the package rooted at /app so the server
# finds frontend/ next to src/ (same layout as the dev checkout).
RUN echo 'demucs==4.0.1' > /tmp/constraints.txt \
    && pip install --no-cache-dir -c /tmp/constraints.txt -e ".[ml,export,server]"

RUN useradd --create-home tabforge \
    # pre-create the cache mount point owned by the app user: a named
    # volume inherits ownership from the image on first creation, and a
    # root-owned one would break model downloads
    && mkdir -p /home/tabforge/.cache \
    && chown -R tabforge:tabforge /home/tabforge/.cache
USER tabforge
ENV HOME=/home/tabforge
# numba (librosa's JIT) must not try to cache next to the root-owned
# site-packages — as a non-root user that fails with "no locator available"
ENV NUMBA_CACHE_DIR=/tmp/numba-cache

EXPOSE 8000
CMD ["uvicorn", "tabforge.server.app:app", "--host", "0.0.0.0", "--port", "8000"]
