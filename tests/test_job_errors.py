"""Regression tests for silent failure modes around demucs and job state."""
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from tabforge.audio import transcribe

try:
    from tabforge.server import app as server_app
    HAVE_SERVER = True
except ImportError:
    HAVE_SERVER = False


class TestSeparateStemsErrors(unittest.TestCase):
    def test_nonzero_exit_becomes_runtime_error(self):
        # demucs failures must surface as a plain Exception (catchable by
        # `except Exception`), never a SystemExit, with stderr attached.
        failed = subprocess.CompletedProcess(
            args=["demucs"], returncode=1,
            stdout="", stderr="some noise\nfatal: could not load model\n")
        with mock.patch.object(transcribe.subprocess, "run",
                               return_value=failed):
            with self.assertRaises(RuntimeError) as ctx:
                transcribe.separate_stems(Path("x.wav"), Path("/tmp/out"))
        self.assertIn("could not load model", str(ctx.exception))
        self.assertNotIsInstance(ctx.exception, SystemExit)

    def test_runs_demucs_in_subprocess(self):
        # The in-process demucs API call must never come back: a SystemExit
        # raised inside it would escape `except Exception` in the server.
        ok = subprocess.CompletedProcess(args=[], returncode=0,
                                         stdout="", stderr="")
        with mock.patch.object(transcribe.subprocess, "run",
                               return_value=ok) as run:
            transcribe.separate_stems(Path("x.wav"), Path("/tmp/out"))
        cmd = run.call_args.args[0]
        self.assertIn("demucs", " ".join(str(c) for c in cmd))


@unittest.skipUnless(HAVE_SERVER, "fastapi is not installed")
class TestJobNeverStuckRunning(unittest.TestCase):
    def test_base_exception_still_marks_job_error(self):
        # Reproduces the original bug: a SystemExit escaping the pipeline
        # left the job in status='running' forever.
        job = server_app.Job(id="t1")
        job.dir = Path("/tmp")
        with mock.patch.object(server_app, "run_transcribe",
                               side_effect=SystemExit(1)):
            with self.assertRaises(SystemExit):
                server_app._run_transcribe(job, server_app.PipelineOptions())
        self.assertEqual(job.status, "error")
        self.assertTrue(job.error)

    def test_plain_exception_sets_error_message(self):
        job = server_app.Job(id="t2")
        job.dir = Path("/tmp")
        with mock.patch.object(server_app, "run_transcribe",
                               side_effect=RuntimeError("demucs failed")):
            server_app._run_transcribe(job, server_app.PipelineOptions())
        self.assertEqual(job.status, "error")
        self.assertIn("demucs failed", job.error)

    def test_analyze_errors_are_reported_too(self):
        job = server_app.Job(id="t3")
        job.dir = Path("/tmp")
        with mock.patch.object(server_app, "run_analyze",
                               side_effect=RuntimeError("no model")):
            server_app._run_analyze(job, Path("x.wav"))
        self.assertEqual(job.status, "error")
        self.assertIn("no model", job.error)


if __name__ == "__main__":
    unittest.main()
