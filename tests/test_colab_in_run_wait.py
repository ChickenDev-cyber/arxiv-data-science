"""Notebook mode must reach the measured runner, with bounded settings."""
import contextlib
import io
import shlex
import unittest

from tests import test_colab_automation as automation


class NotebookInRunWaitTests(unittest.TestCase):
    def setUp(self):
        self.fixture = automation.NotebookAutomationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def worker(self):
        return self.fixture.worker()
    def test_wait_mode_reaches_the_measured_runner_and_stays_in_invocation(self):
        worker = self.worker()
        worker.references_rate_limit_mode = 'wait'
        worker.stop_after_seconds = 60
        batch = worker.make_batch('pilot', 'cold-01')
        command = shlex.split(batch['command'][-1])
        self.assertIn('--references-rate-limit-mode', command)
        self.assertEqual(command[command.index('--references-rate-limit-mode')+1], 'wait')
        with contextlib.redirect_stdout(io.StringIO()):
            worker.execute_batch(batch)
            report = worker.inspect_batch(batch)
        self.assertEqual(report['max_attempt_id'], 2)

    def test_worker_constructor_rejects_wait_without_positive_time_budget(self):
        import tempfile
        from pathlib import Path
        from tests.test_colab_automation import REPO, notebook_namespace
        ns = notebook_namespace()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for mode, seconds in [('unknown',60), ('wait',None)]:
                with self.subTest(mode=mode), self.assertRaises(ValueError):
                    ns['NotebookBatchWorker'](repo_dir=REPO, env=ns['PIPELINE_ENV'],
                        work_root=root, output_root=root/'corpus', drive_root=root,
                        owner='trong', run_id='wait', pipeline_commit='f'*40, initial_args=['--mock'],
                        references_rate_limit_mode=mode, stop_after_seconds=seconds)
