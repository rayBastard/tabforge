"""
HTTP server. The same code serves:
  - the desktop app (pywebview opens a window on localhost),
  - the browser (just open the address),
  - in the future — a mobile client (this is already a ready-made API).

Jobs take a long time (minutes), so: POST creates a job, the frontend polls
the progress and downloads the files when done.

Public-deployment knobs (environment variables):
  TABFORGE_TOKEN          if set, every /api/* request must carry it
                          (X-API-Token header, or ?token= for downloads)
  TABFORGE_WORKERS        parallel pipeline workers (default 1)
  TABFORGE_MAX_UPLOAD_MB  upload size limit (default 30)
  TABFORGE_MAX_DURATION_S audio length limit, seconds (default 600)
  TABFORGE_JOB_TTL_S      keep finished jobs this long (default 7200)
  TABFORGE_MAX_JOBS       stored-jobs cap (default 20)
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import mkdtemp

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..core.fretboard import TUNINGS
from ..pipeline import STAGES, PipelineOptions, run_pipeline

if getattr(sys, "frozen", False):
    # PyInstaller bundle: data files are unpacked next to the binary
    FRONTEND = Path(getattr(sys, "_MEIPASS")) / "frontend"
else:
    FRONTEND = Path(__file__).resolve().parent.parent.parent.parent / "frontend"
WORK_ROOT = Path(mkdtemp(prefix="tabforge_"))

TOKEN = os.environ.get("TABFORGE_TOKEN", "")
WORKERS = int(os.environ.get("TABFORGE_WORKERS", "1"))
MAX_UPLOAD_BYTES = int(float(os.environ.get("TABFORGE_MAX_UPLOAD_MB", "30")) * 1e6)
MAX_DURATION_S = float(os.environ.get("TABFORGE_MAX_DURATION_S", "600"))
JOB_TTL_S = float(os.environ.get("TABFORGE_JOB_TTL_S", "7200"))
MAX_JOBS = int(os.environ.get("TABFORGE_MAX_JOBS", "20"))

CLEANUP_INTERVAL_S = 300.0


@dataclass
class Job:
    id: str
    status: str = "queued"            # queued | running | done | error
    stage: str = ""
    log: list[str] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)
    error: str = ""
    backing: str = ""                 # download URL of the backing track
    dir: Path | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def public(self) -> dict:
        with self.lock:
            return {
                "id": self.id, "status": self.status, "stage": self.stage,
                "stages": list(STAGES), "log": list(self.log[-30:]),
                "results": list(self.results), "error": self.error,
                "backing": self.backing,
            }


JOBS: dict[str, Job] = {}
POOL = ThreadPoolExecutor(max_workers=max(1, WORKERS))

app = FastAPI(title="TabForge")


# ---------------------------------------------------------------------------
# Auth: when TABFORGE_TOKEN is set, every /api/* call must present it.
# Download links and alphaTab can't send headers, so ?token= also counts.
# ---------------------------------------------------------------------------

@app.middleware("http")
async def require_token(request: Request, call_next):
    if TOKEN and request.url.path.startswith("/api/"):
        supplied = (request.headers.get("x-api-token")
                    or request.query_params.get("token"))
        if supplied != TOKEN:
            return JSONResponse({"detail": "invalid or missing API token"},
                                status_code=401)
    return await call_next(request)


# ---------------------------------------------------------------------------
# Job lifecycle: finished jobs expire after JOB_TTL_S, the store is capped
# at MAX_JOBS, and a background thread sweeps periodically.
# ---------------------------------------------------------------------------

def _drop_job(job_id: str) -> None:
    job = JOBS.pop(job_id, None)
    if job and job.dir:
        shutil.rmtree(job.dir, ignore_errors=True)


def cleanup_jobs(now: float | None = None) -> int:
    """Remove finished jobs older than JOB_TTL_S. Returns how many."""
    now = now if now is not None else time.time()
    expired = []
    for job_id, job in list(JOBS.items()):
        with job.lock:
            done = job.status in ("done", "error")
            stamp = job.finished_at
        if done and stamp is not None and now - stamp > JOB_TTL_S:
            expired.append(job_id)
    for job_id in expired:
        _drop_job(job_id)
    return len(expired)


def _evict_for_capacity() -> bool:
    """Make room for one more job. Finished jobs go first (oldest first);
    returns False when everything is still queued/running."""
    if len(JOBS) < MAX_JOBS:
        return True
    finished = sorted(
        (j for j in JOBS.values()
         if j.status in ("done", "error")),
        key=lambda j: j.finished_at or j.created_at)
    if not finished:
        return False
    _drop_job(finished[0].id)
    return len(JOBS) < MAX_JOBS


def _cleanup_loop() -> None:
    while True:
        time.sleep(CLEANUP_INTERVAL_S)
        cleanup_jobs()


@app.on_event("startup")
def _start_cleanup_thread() -> None:
    threading.Thread(target=_cleanup_loop, daemon=True,
                     name="tabforge-job-cleanup").start()


# ---------------------------------------------------------------------------
# Upload validation
# ---------------------------------------------------------------------------

def probe_duration(path: Path) -> float:
    """Seconds of audio; raises when the file is not decodable audio."""
    import librosa

    return float(librosa.get_duration(path=str(path)))


def _sanitized_upload_name(filename: str | None) -> str:
    """Only the extension of the client-supplied name survives: the name
    itself lands in a filesystem path, so traversal characters must never
    reach it."""
    suffix = Path(filename or "").suffix.lower()
    if not (2 <= len(suffix) <= 8) or not suffix[1:].isalnum():
        suffix = ".bin"
    return f"upload{suffix}"


async def _save_upload(file: UploadFile, target: Path) -> None:
    size = 0
    with target.open("wb") as fh:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    413, f"File is too large (limit "
                         f"{MAX_UPLOAD_BYTES // 1_000_000} MB)")
            fh.write(chunk)
    if size == 0:
        raise HTTPException(422, "Empty upload")


# ---------------------------------------------------------------------------
# The worker
# ---------------------------------------------------------------------------

def _run(job: Job, audio: Path, opts: PipelineOptions) -> None:
    def progress(stage: str, msg: str) -> None:
        with job.lock:
            job.stage = stage
            job.log.append(msg)

    with job.lock:
        job.status = "running"
    try:
        results = run_pipeline(audio, job.dir / "out", opts, progress)
        with job.lock:
            job.results = [
                r.to_dict(lambda stem, p:
                          f"/api/jobs/{job.id}/files/{stem}/{p.name}")
                for r in results
            ]
            if (job.dir / "out" / "backing" / "backing.wav").is_file():
                job.backing = f"/api/jobs/{job.id}/files/backing/backing.wav"
            job.status = "done"
            job.stage = "done"
    except Exception as e:  # noqa: BLE001 — shown to the user
        with job.lock:
            job.status = "error"
            job.error = str(e)
    finally:
        # Whatever escaped above (BaseException included), a job must
        # never be left in "running" — the frontend would poll forever.
        with job.lock:
            if job.status == "running":
                job.status = "error"
                job.error = job.error or "job crashed unexpectedly"
            job.finished_at = time.time()


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.post("/api/jobs")
async def create_job(
    file: UploadFile,
    stems: str = "guitar,bass",
    tuning: str = "standard",
    separate: bool = True,
    split_guitars: bool = False,
) -> dict:
    if tuning not in TUNINGS:
        raise HTTPException(400, f"Unknown tuning: {tuning}")

    cleanup_jobs()
    if not _evict_for_capacity():
        raise HTTPException(
            429, "The server is at its job limit right now — try again "
                 "in a few minutes")

    job = Job(id=uuid.uuid4().hex[:12])
    job.dir = WORK_ROOT / job.id
    job.dir.mkdir(parents=True)

    audio = job.dir / _sanitized_upload_name(file.filename)
    try:
        await _save_upload(file, audio)
        try:
            duration = probe_duration(audio)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                422, "This does not look like a decodable audio file")
        if duration > MAX_DURATION_S:
            raise HTTPException(
                413, f"Audio is too long ({duration:.0f}s; limit "
                     f"{MAX_DURATION_S:.0f}s)")
    except HTTPException:
        shutil.rmtree(job.dir, ignore_errors=True)
        raise

    opts = PipelineOptions(
        stems=tuple(s.strip() for s in stems.split(",") if s.strip()),
        tuning=tuning,
        separate=separate,
        split_guitars=split_guitars,
    )
    JOBS[job.id] = job
    POOL.submit(_run, job, audio, opts)
    return {"id": job.id}


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job.public()


@app.get("/api/jobs/{job_id}/files/{stem}/{name}")
async def job_file(job_id: str, stem: str, name: str) -> FileResponse:
    job = JOBS.get(job_id)
    if not job or not job.dir:
        raise HTTPException(404, "Job not found")
    path = (job.dir / "out" / stem / name).resolve()
    if not path.is_file() or job.dir.resolve() not in path.parents:
        raise HTTPException(404, "File not found")
    return FileResponse(path, filename=name)


@app.get("/api/tunings")
async def tunings() -> dict:
    return {"tunings": sorted(TUNINGS)}


# the frontend goes last so it doesn't intercept /api/*
app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="frontend")
