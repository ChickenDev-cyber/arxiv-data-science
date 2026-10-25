from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from arxiv_pipeline.runner.models import RunOptions, paper_spec
from arxiv_pipeline.runner.state import StateStore, OutputLock, load_options
from .helpers import PAPER, metadata_result


class StateTests(unittest.TestCase):
    def test_reopen_and_failed_retry_preserve_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            options = RunOptions("mock-demo", Path(tmp), ("trong",), True)
            spec = paper_spec(PAPER, "metadata")
            with StateStore(Path(tmp)/"state.sqlite", options.run_id) as s:
                s.open_run(options); s.ensure_task(spec)
                a = s.mark_running(spec.key); s.record(a, metadata_result(), [])
                s.prepare(a, {"artifacts": [], "operations": []}); s.complete(a, True)
                b = s.mark_running(spec.key)
                failed = metadata_result(); failed.update(status="failed", data=None, error={
                    "code": "network_error", "message": "timeout", "retryable": True, "http_status": None})
                s.record(b, failed, []); s.complete(b, False)
            with StateStore(Path(tmp)/"state.sqlite", options.run_id) as s:
                s.open_run(options)
                self.assertEqual(s.get_task(spec.key)["accepted_result"]["status"], "success")
                self.assertEqual(len(list(s.iter_results(accepted_only=False))), 2)
                with self.assertRaises(ValueError): s.open_run(replace(options, is_mock=False))
            self.assertEqual(load_options(Path(tmp)/"state.sqlite", "mock-demo"), options)

    def test_unowned_corpus_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / "raw").mkdir()
            with StateStore(root / "state.sqlite", "test") as s:
                with self.assertRaises(ValueError): s.open_run(RunOptions("test", root, ("trong",)))

    def test_lock_is_owned_by_process_not_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = ('from pathlib import Path; from arxiv_pipeline.runner.state import OutputLock; '
                    f'lock=OutputLock(Path({tmp!r})); lock.__enter__(); print("locked",flush=True); input()')
            child = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(), "locked")
                with self.assertRaises(ValueError):
                    with OutputLock(Path(tmp)): pass
            finally:
                child.kill(); child.communicate(timeout=5)
            with OutputLock(Path(tmp)): self.assertTrue((Path(tmp)/".runner.lock").exists())
