"""Limits, validation, auth, and job-lifecycle tests for the server API.
Uses FastAPI's TestClient (httpx); skipped in core-only CI installs."""
import io
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
        # keep the pipeline out of these tests
        patcher = mock.patch.object(srv, "run_pipeline", return_value=[])
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

    def test_unknown_tuning_is_400(self):
        res = post_job(self.client, tiny_wav_bytes(), tuning="lute")
        self.assertEqual(res.status_code, 400)


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


if __name__ == "__main__":
    unittest.main()
