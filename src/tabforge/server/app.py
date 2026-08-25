"""
HTTP server. The same code serves:
  - the desktop app (pywebview opens a window on localhost),
  - the browser (just open the address),
  - in the future — a mobile client (this is already a ready-made API).

Jobs take a long time (minutes), so: POST creates a job, the frontend polls
the progress and downloads the files when done.
"""

from __future__ import annotations

import shutil
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import mkdtemp

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..core.fretboard import TUNINGS
from ..pipeline import STAGES, PipelineOptions, run_pipeline

if getattr(sys, "frozen", False):
    # PyInstaller bundle: data files are unpacked next to the binary
    FRONTEND = Path(getattr(sys, "_MEIPASS")) / "frontend"
else:
    FRONTEND = Path(__file__).resolve().parent.parent.parent.parent / "frontend"
WORK_ROOT = Path(mkdtemp(prefix="tabforge_"))


@dataclass
class Job:
    id: str
    status: str = "queued"            # queued | running | done | error
    stage: str = ""
    log: list[str] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)
    error: str = ""
    dir: Path | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def public(self) -> dict:
        with self.lock:
            return {
                "id": self.id, "status": self.status, "stage": self.stage,
                "stages": list(STAGES), "log": list(self.log[-30:]),
                "results": list(self.results), "error": self.error,
            }


JOBS: dict[str, Job] = {}
POOL = ThreadPoolExecutor(max_workers=1)   # demucs is hungry — one at a time

app = FastAPI(title="TabForge")


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
                {
                    "stem": r.stem, "bpm": round(r.bpm, 1), "key": r.key,
                    "notes": r.note_count, "ascii": r.ascii_tab,
                    "warnings": list(r.warnings),
                    "files": {ext: f"/api/jobs/{job.id}/files/{r.stem}/{p.name}"
                              for ext, p in r.files.items()},
                }
                for r in results
            ]
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
    job = Job(id=uuid.uuid4().hex[:12])
    job.dir = WORK_ROOT / job.id
    job.dir.mkdir(parents=True)

    audio = job.dir / (file.filename or "upload.mp3")
    with audio.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)

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
