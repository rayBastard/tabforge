"""Limits, validation, auth, and job-lifecycle tests for the server API.
Uses FastAPI's TestClient (httpx); skipped in core-only CI installs."""
import io
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

try:
    from fastapi.testclient import TestClient

    from tabforge.server import app as srv
    HAVE_SERVER = True
except ImportError:
    HAVE_SERVER = False

try:
    import numpy as np
    import soundfile as sf
    HAVE_AUDIO = True
except ImportError:
    HAVE_AUDIO = False


def tiny_wav_bytes(seconds: float = 1.0, sr: int = 8000) -> bytes:
    t = np.arange(int(sr * seconds)) / sr
    buf = io.BytesIO()
    sf.write(buf, (0.3 * np.sin(2 * np.pi * 220 * t)).astype("float32"),
             sr, format="WAV")
    return buf.getvalue()


def post_job(client, content: bytes, filename: str = "song.wav", **params):
    return client.post("/api/jobs", params=params,
                       files={"file": (filename, content, "audio/wav")})


@unittest.skipUnless(HAVE_SERVER, "fastapi/httpx are not installed")
class ServerTestCase(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(srv.app)
        srv.JOBS.clear()
        # keep the real pipeline out of these tests
        from tabforge.pipeline import AnalyzeResult
        self.fake_analysis = AnalyzeResult(
            stems={"guitar": Path("/g.wav"), "drums": Path("/d.wav")},
            analysis={}, bpm=120.0, beats=[], tempo_reliable=True, key=None)
        for name, value in (("run_analyze", self.fake_analysis),
                            ("run_transcribe", [])):
            patcher = mock.patch.object(srv, name, return_value=value)
            self.addCleanup(patcher.stop)
            patcher.start()

    def set(self, attr, value):
        original = getattr(srv, attr)
        setattr(srv, attr, value)
        self.addCleanup(setattr, srv, attr, original)


@unittest.skipUnless(HAVE_AUDIO, "numpy/soundfile are not installed")
class TestUploadLimits(ServerTestCase):
    def test_oversized_upload_is_413(self):
        self.set("MAX_UPLOAD_BYTES", 1000)
        res = post_job(self.client, b"x" * 5000)
        self.assertEqual(res.status_code, 413)
        self.assertIn("too large", res.json()["detail"])

    def test_garbage_is_422(self):
        res = post_job(self.client, b"this is definitely not audio " * 100)
        self.assertEqual(res.status_code, 422)
        self.assertIn("audio", res.json()["detail"])

    def test_empty_upload_is_422(self):
        res = post_job(self.client, b"")
        self.assertEqual(res.status_code, 422)

    def test_too_long_audio_is_413(self):
        self.set("MAX_DURATION_S", 0.5)
        res = post_job(self.client, tiny_wav_bytes(seconds=2.0))
        self.assertEqual(res.status_code, 413)
        self.assertIn("too long", res.json()["detail"])

    def test_valid_audio_is_accepted(self):
        res = post_job(self.client, tiny_wav_bytes())
        self.assertEqual(res.status_code, 200)
        self.assertIn("id", res.json())

    def test_limits_endpoint_reports_the_upload_cap(self):
        # the UI pre-checks the file size against this before uploading
        self.set("MAX_UPLOAD_BYTES", 200_000_000)
        res = self.client.get("/api/limits")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["max_upload_mb"], 200)
        self.assertIn("max_duration_s", body)

# tuning validation moved to POST /transcribe — see TestTwoStepFlow


@unittest.skipUnless(HAVE_AUDIO, "numpy/soundfile are not installed")
class TestFilenameSanitization(ServerTestCase):
    def test_traversal_name_cannot_escape_job_dir(self):
        res = post_job(self.client, tiny_wav_bytes(),
                       filename="../../../../etc/evil.wav")
        self.assertEqual(res.status_code, 200)
        job = srv.JOBS[res.json()["id"]]
        stored = list(job.dir.iterdir())
        self.assertEqual([p.name for p in stored], ["upload.wav"])
        self.assertTrue(all(job.dir in p.parents for p in stored))

    def test_weird_extension_becomes_bin(self):
        self.assertEqual(srv._sanitized_upload_name("x.w!v"), "upload.bin")
        self.assertEqual(srv._sanitized_upload_name(None), "upload.bin")
        self.assertEqual(srv._sanitized_upload_name("a/b/c.flac"), "upload.flac")


@unittest.skipUnless(HAVE_AUDIO, "numpy/soundfile are not installed")
class TestTwoStepFlow(ServerTestCase):
    def _analyzed_job(self):
        res = post_job(self.client, tiny_wav_bytes())
        self.assertEqual(res.status_code, 200)
        job_id = res.json()["id"]
        # POOL runs in threads; wait for the analyze stub to land
        for _ in range(100):
            data = self.client.get(f"/api/jobs/{job_id}").json()
            if data["status"] in ("analyzed", "error"):
                break
            time.sleep(0.02)
        return job_id, data

    def test_job_stops_at_analyzed(self):
        job_id, data = self._analyzed_job()
        self.assertEqual(data["status"], "analyzed")
        self.assertIn("analysis", data)

    def test_transcribe_needs_analyzed_state(self):
        res = post_job(self.client, tiny_wav_bytes())
        job_id = res.json()["id"]
        srv.JOBS[job_id].status = "running"      # force not-ready
        r = self.client.post(f"/api/jobs/{job_id}/transcribe",
                             json={"stems": ["guitar"]})
        self.assertEqual(r.status_code, 409)

    def test_transcribe_validates_selection(self):
        job_id, _ = self._analyzed_job()
        r = self.client.post(f"/api/jobs/{job_id}/transcribe",
                             json={"stems": []})
        self.assertEqual(r.status_code, 400)

    def test_subdivision_is_validated_and_reaches_options(self):
        job_id, _ = self._analyzed_job()
        r = self.client.post(f"/api/jobs/{job_id}/transcribe",
                             json={"stems": ["guitar"], "subdivision": 5})
        self.assertEqual(r.status_code, 400)
        r = self.client.post(f"/api/jobs/{job_id}/transcribe",
                             json={"stems": ["guitar"], "subdivision": 3})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(srv.JOBS[job_id].opts.subdivision, 3)
        for _ in range(100):               # let the stubbed run finish
            if self.client.get(f"/api/jobs/{job_id}").json()["status"] == "done":
                break
            time.sleep(0.02)
        # default is the steady eighth grid
        r = self.client.post(f"/api/jobs/{job_id}/transcribe",
                             json={"stems": ["guitar"]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(srv.JOBS[job_id].opts.subdivision, 2)
        r = self.client.post(f"/api/jobs/{job_id}/transcribe",
                             json={"stems": ["theremin"]})
        self.assertEqual(r.status_code, 400)
        r = self.client.post(f"/api/jobs/{job_id}/transcribe",
                             json={"stems": ["guitar"], "tuning": "lute"})
        self.assertEqual(r.status_code, 400)

    def test_transcribe_runs_from_cached_stems(self):
        job_id, _ = self._analyzed_job()
        r = self.client.post(f"/api/jobs/{job_id}/transcribe",
                             json={"stems": ["guitar"]})
        self.assertEqual(r.status_code, 200)
        for _ in range(100):
            data = self.client.get(f"/api/jobs/{job_id}").json()
            if data["status"] in ("done", "error"):
                break
            time.sleep(0.02)
        self.assertEqual(data["status"], "done")
        srv.run_analyze.assert_called_once()     # demucs never reran
        # a second selection re-transcribes without re-analyzing
        r = self.client.post(f"/api/jobs/{job_id}/transcribe",
                             json={"stems": ["drums"]})
        self.assertEqual(r.status_code, 200)
        srv.run_analyze.assert_called_once()


@unittest.skipUnless(HAVE_AUDIO, "numpy/soundfile are not installed")
class TestRepin(ServerTestCase):
    def test_repin_requires_done_state(self):
        res = post_job(self.client, tiny_wav_bytes())
        job_id = res.json()["id"]
        r = self.client.post(f"/api/jobs/{job_id}/repin",
                             json={"part": "guitar", "qticks": 0,
                                   "pitch": 60, "string": 2})
        self.assertEqual(r.status_code, 409)

    def test_repin_end_to_end_on_real_parts(self):
        # a real (tiny) pipeline state: save a part, then pin through
        # apply_repin directly — the endpoint's core path
        import json
        import tempfile

        from tabforge.core.fretboard import NoteEvent
        from tabforge.pipeline import (AnalyzeResult, PipelineOptions,
                                       _save_part_state, apply_repin)

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "guitar").mkdir()
            (out / "song").mkdir()
            notes = [NoteEvent(64 + i, i * 0.5, 0.4) for i in range(3)]
            _save_part_state(out, "guitar", notes, [], "standard", "guitar")
            shared = AnalyzeResult(stems={}, analysis={}, bpm=120.0,
                                   beats=[], tempo_reliable=True, key=None)
            result = apply_repin(out, "guitar", tick=0, pitch=64,
                                 string=3, shared=shared,
                                 opts=PipelineOptions())
            self.assertIsNone(result["prev"])
            self.assertIn("9", result["ascii"], "pinned fret must appear")
            state = json.loads((out / "parts.json").read_text())
            self.assertEqual(state["guitar"]["pins"], {"0": 3})
            self.assertTrue((out / "song" / "song.gp5").is_file())
            # unpin restores and reports the previous pin
            result = apply_repin(out, "guitar", tick=0, pitch=64,
                                 string=None, shared=shared,
                                 opts=PipelineOptions())
            self.assertEqual(result["prev"], 3)

    def test_repin_unknown_note_is_400_shaped(self):
        import tempfile

        from tabforge.core.fretboard import NoteEvent
        from tabforge.pipeline import (AnalyzeResult, PipelineOptions,
                                       _save_part_state, apply_repin)

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "guitar").mkdir()
            _save_part_state(out, "guitar",
                             [NoteEvent(60, 0.0, 0.5)], [],
                             "standard", "guitar")
            shared = AnalyzeResult(stems={}, analysis={}, bpm=120.0,
                                   beats=[], tempo_reliable=True, key=None)
            with self.assertRaises(ValueError):
                apply_repin(out, "guitar", tick=40, pitch=99,
                            string=1, shared=shared, opts=PipelineOptions())


class TestToken(ServerTestCase):
    def test_no_token_configured_is_open(self):
        self.assertEqual(self.client.get("/api/tunings").status_code, 200)

    def test_token_required_when_configured(self):
        self.set("TOKEN", "sekret")
        self.assertEqual(self.client.get("/api/tunings").status_code, 401)
        ok = self.client.get("/api/tunings", headers={"X-API-Token": "sekret"})
        self.assertEqual(ok.status_code, 200)
        bad = self.client.get("/api/tunings", headers={"X-API-Token": "nope"})
        self.assertEqual(bad.status_code, 401)

    def test_query_token_works_for_downloads(self):
        self.set("TOKEN", "sekret")
        res = self.client.get("/api/tunings?token=sekret")
        self.assertEqual(res.status_code, 200)

    def test_frontend_stays_open(self):
        self.set("TOKEN", "sekret")
        self.assertEqual(self.client.get("/").status_code, 200)


class TestPwaAssets(ServerTestCase):
    def test_manifest_and_service_worker_are_served(self):
        m = self.client.get("/manifest.json")
        self.assertEqual(m.status_code, 200)
        self.assertEqual(m.json()["display"], "standalone")
        sw = self.client.get("/sw.js")
        self.assertEqual(sw.status_code, 200)
        self.assertIn("tabforge-shell-", sw.text)
        for size in (192, 512):
            self.assertEqual(
                self.client.get(f"/icons/icon-{size}.png").status_code, 200)


class TestJobLifecycle(ServerTestCase):
    def _fake_job(self, status="done", age_s=0.0):
        job = srv.Job(id=f"j{len(srv.JOBS)}")
        job.dir = srv.WORK_ROOT / job.id
        job.dir.mkdir(parents=True, exist_ok=True)
        job.status = status
        if status in ("done", "error"):
            job.finished_at = time.time() - age_s
        srv.JOBS[job.id] = job
        return job

    def test_expired_jobs_are_cleaned(self):
        self.set("JOB_TTL_S", 100.0)
        old = self._fake_job(age_s=1000.0)
        fresh = self._fake_job(age_s=10.0)
        running = self._fake_job(status="running")
        removed = srv.cleanup_jobs()
        self.assertEqual(removed, 1)
        self.assertNotIn(old.id, srv.JOBS)
        self.assertFalse(old.dir.exists(), "expired job dir must be deleted")
        self.assertIn(fresh.id, srv.JOBS)
        self.assertIn(running.id, srv.JOBS)

    @unittest.skipUnless(HAVE_AUDIO, "numpy/soundfile are not installed")
    def test_full_store_of_running_jobs_is_429(self):
        self.set("MAX_JOBS", 2)
        self._fake_job(status="running")
        self._fake_job(status="running")
        res = post_job(self.client, tiny_wav_bytes())
        self.assertEqual(res.status_code, 429)

    @unittest.skipUnless(HAVE_AUDIO, "numpy/soundfile are not installed")
    def test_finished_job_is_evicted_to_make_room(self):
        self.set("MAX_JOBS", 2)
        finished = self._fake_job(status="done", age_s=5.0)
        self._fake_job(status="running")
        res = post_job(self.client, tiny_wav_bytes())
        self.assertEqual(res.status_code, 200)
        self.assertNotIn(finished.id, srv.JOBS)


@unittest.skipUnless(HAVE_AUDIO, "numpy/soundfile are not installed")
class TestCancel(ServerTestCase):
    def _wait_status(self, job_id: str, wanted: str, timeout_s: float = 5.0):
        deadline = time.time() + timeout_s
        status = None
        while time.time() < deadline:
            status = self.client.get(f"/api/jobs/{job_id}").json()["status"]
            if status == wanted:
                return status
            time.sleep(0.02)
        return status

    def test_cancel_during_analyze_ends_the_job(self):
        started = threading.Event()

        def slow_analyze(audio, out_dir, progress, cancel_token=None):
            started.set()
            for _ in range(400):           # ~4 s unless canceled
                progress("separate", "working")
                time.sleep(0.01)
            return self.fake_analysis

        with mock.patch.object(srv, "run_analyze", side_effect=slow_analyze):
            job_id = post_job(self.client, tiny_wav_bytes()).json()["id"]
            self.assertTrue(started.wait(5), "worker never started")
            res = self.client.post(f"/api/jobs/{job_id}/cancel")
            self.assertEqual(res.status_code, 200)
            self.assertEqual(self._wait_status(job_id, "canceled"), "canceled")

    def test_cancel_during_transcribe_returns_to_the_picker(self):
        started = threading.Event()

        def slow_transcribe(out_dir, analyzed, opts, progress):
            started.set()
            for _ in range(400):
                progress("transcribe", "working")
                time.sleep(0.01)
            return []

        job_id = post_job(self.client, tiny_wav_bytes()).json()["id"]
        self.assertEqual(self._wait_status(job_id, "analyzed"), "analyzed")
        with mock.patch.object(srv, "run_transcribe",
                               side_effect=slow_transcribe):
            res = self.client.post(f"/api/jobs/{job_id}/transcribe",
                                   json={"stems": ["guitar"]})
            self.assertEqual(res.status_code, 200)
            self.assertTrue(started.wait(5), "worker never started")
            self.client.post(f"/api/jobs/{job_id}/cancel")
            # the cached separation survives: back to picking instruments
            self.assertEqual(self._wait_status(job_id, "analyzed"), "analyzed")

    def test_cancel_needs_a_running_job(self):
        res = self.client.post("/api/jobs/nonexistent/cancel")
        self.assertEqual(res.status_code, 404)
        job_id = post_job(self.client, tiny_wav_bytes()).json()["id"]
        self._wait_status(job_id, "analyzed")
        res = self.client.post(f"/api/jobs/{job_id}/cancel")
        self.assertEqual(res.status_code, 409)

    def test_abort_separation_kills_the_registered_process(self):
        from tabforge.audio import transcribe as T

        class FakeProc:
            killed = False
            def kill(self):
                self.killed = True

        proc = FakeProc()
        with T._ACTIVE_LOCK:
            T._ACTIVE["tok"] = proc
        try:
            self.assertTrue(T.abort_separation("tok"))
            self.assertTrue(proc.killed)
        finally:
            with T._ACTIVE_LOCK:
                T._ACTIVE.pop("tok", None)
        self.assertFalse(T.abort_separation("missing"))


if __name__ == "__main__":
    unittest.main()
