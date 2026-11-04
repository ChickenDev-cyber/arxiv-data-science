"""Reference outage pauses and smoke runs must finish backup before stopping."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.test_colab_automation import notebook_namespace, REPO
from tests.test_colab_automation_controller import FakeBackend


class ReferenceRecoveryNotebookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ns = notebook_namespace()
        self.journal = self.root / 'session.json'

    def session(self, backend):
        return self.ns['AutoCrawlSession'](backend, owner='trong', run_id='mock-key',
            pipeline_commit='f'*40, journal_path=self.journal, initial_mode='resume',
            initial_measurement='resume-27', max_batches=200)

    def limited_report(self):
        return {'run_state': 'paused', 'pause_reason': 'references_rate_limited',
                'max_attempt_id': 30, 'retryable_tasks': 2, 'progress_key': 'same',
                'summary': {}, 'scope_complete': True, 'issues': []}

    def test_failed_preflight_never_creates_pending_or_executes_batch(self):
        backend = FakeBackend()
        def preflight():
            raise RuntimeError('HTTP 429')
        backend.preflight = preflight
        with self.assertRaisesRegex(RuntimeError, '429'), contextlib.redirect_stdout(io.StringIO()):
            self.session(backend).run()
        if self.journal.exists():
            self.assertIsNone(json.loads(self.journal.read_text())['pending'])
        self.assertFalse(any(c[0] == 'execute_batch' for c in backend.calls))

    def test_429_pause_backs_up_then_stops_without_consuming_retry_or_stall_budget(self):
        backend = FakeBackend(step_responses=[self.limited_report()])
        session = self.session(backend)
        session.previous_progress = 'same'
        session.retry_passes = 1
        session.retry_before_attempt = 20
        session.stall_retries = 1
        with contextlib.redirect_stdout(io.StringIO()):
            result = session.run()
        self.assertEqual(result['stop_reason'], 'rate_limited')
        self.assertFalse(result['terminal'])
        self.assertEqual((result['batches'], result['retry_passes'], result['retry_before_attempt']), (1, 1, 20))
        saved = json.loads(self.journal.read_text())
        self.assertEqual(saved['stall_retries'], 1)
        self.assertIsNone(saved['pending'])
        self.assertEqual(len([c for c in backend.calls if c[0] == 'backup_batch']), 1)
        self.assertEqual(len([c for c in backend.calls if c[0] == 'execute_batch']), 1)

    def test_failed_backup_recovers_without_key_probe_or_second_execution(self):
        backend = FakeBackend(step_responses=[self.limited_report()])
        backend.fail_backup = True
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, 'Backup'):
            self.session(backend).run()
        backend.fail_backup = False
        backend.preflight = lambda: self.fail('A pending backup must not probe the API')
        with contextlib.redirect_stdout(io.StringIO()):
            result = self.session(backend).run()
        self.assertEqual(result['stop_reason'], 'rate_limited')
        self.assertEqual(result['batches'], 1)
        self.assertEqual(len([c for c in backend.calls if c[0] == 'execute_batch']), 1)

    def test_one_batch_smoke_preserves_cumulative_budget_and_can_continue(self):
        reports = [{'run_state': 'paused', 'retryable_tasks': 0, 'max_attempt_id': i,
                    'progress_key': i, 'scope_complete': True, 'issues': []} for i in (11, 12)]
        backend = FakeBackend(step_responses=reports)
        session = self.session(backend)
        session.batches = 26
        with contextlib.redirect_stdout(io.StringIO()):
            result = session.run(max_batches_this_call=1)
        self.assertEqual((result['batches'], result['terminal'], result['stop_reason']), (27, False, 'session_limit'))
        with contextlib.redirect_stdout(io.StringIO()):
            result = self.session(backend).run(max_batches_this_call=1)
        self.assertEqual(result['batches'], 28)
        self.assertEqual(result['retry_passes'], 0)

    def worker(self, **kwargs):
        return self.ns['NotebookBatchWorker'](repo_dir=REPO, env=self.ns['PIPELINE_ENV'],
            work_root=self.root, output_root=self.root/'corpus', drive_root=self.root,
            owner='trong', run_id='mock-key', pipeline_commit='f'*40,
            initial_args=['--mock', '--owner', 'trong', '--max-candidates', '1', '--max-papers', '1'],
            expected_mock=True, **kwargs)

    def test_worker_controls_reach_measured_runner_and_pause_file_is_revision_checked(self):
        worker = self.worker(stop_after_tasks=500, stop_after_seconds=0.000001,
                             references_rate_limit_threshold=2)
        batch = worker.make_batch('pilot', 'cold-01')
        with contextlib.redirect_stdout(io.StringIO()):
            worker.execute_batch(batch)
            report = worker.inspect_batch(batch)
        self.assertEqual(report['pause_reason'], 'time_limit')
        path = worker.output_root/'runs/mock-key/execution.json'
        saved = json.loads(path.read_text())
        saved['max_attempt_id'] += 1
        path.write_text(json.dumps(saved))
        with self.assertRaisesRegex(ValueError, 'execution|pause|trạng thái'):
            worker._run_state()

    def test_real_worker_requires_secret_before_probe_and_captures_authenticated_env(self):
        worker = self.worker(require_s2_key=True)
        worker.expected_mock = False
        worker.env.pop('S2_API_KEY', None)
        with self.assertRaisesRegex(ValueError, 'S2_API_KEY'):
            worker.preflight()
        self.assertFalse((self.root/'measurements').exists())
        worker.env['S2_API_KEY'] = 'test-secret'
        calls = []
        def capture(args, **kwargs):
            calls.append(args)
            return {'ok': True, 'http_status': 200, 'returned_count': 0}
        with patch.object(worker, '_capture', side_effect=capture), patch('time.sleep'):
            worker.preflight()
        self.assertEqual(len(calls), 1)
        self.assertIn('arxiv_pipeline.references.preflight', calls[0])
        self.assertNotIn('test-secret', str(calls))

    def test_measured_429_batch_is_verified_on_drive_and_later_resume_retries_once(self):
        import shlex
        worker = self.worker(stop_after_tasks=50, references_rate_limit_threshold=1)
        adapter = """import sys
from arxiv_pipeline.runner import cli
from arxiv_pipeline.runner.factory import MockModuleFactory
from arxiv_pipeline.runner.models import ModuleSet
class Factory(MockModuleFactory):
 def build(self, options, checkpoints):
  modules=super().build(options, checkpoints)
  original=modules.references
  class References:
   def fetch_references(self, task, context):
    result=original.fetch_references(task, context)
    result.update(status='failed',data=None,error={'code':'rate_limited','message':'offline 429','retryable':True,'http_status':429})
    return result
  return ModuleSet(modules.discovery,modules.metadata,modules.sources,References())
cli.MockModuleFactory=Factory
raise SystemExit(cli.main(sys.argv[1:]))
"""
        real_cli = worker.cli
        def limited(args, **kwargs):
            args = list(args)
            if 'arxiv_pipeline.benchmark' in args:
                index = args.index('--cmd') + 1
                runner = shlex.split(args[index])
                args[index] = shlex.join([runner[0], '-c', adapter, *runner[3:]])
            return real_cli(args, **kwargs)
        worker.cli = limited
        session = self.ns['AutoCrawlSession'](worker, owner='trong', run_id='mock-key',
            pipeline_commit='f'*40, journal_path=self.journal, initial_mode='pilot')
        with contextlib.redirect_stdout(io.StringIO()):
            stopped = session.run()
        self.assertEqual(stopped['stop_reason'], 'rate_limited')
        remote = worker.drive_root/'trong/mock-key/cold-01'
        self.assertTrue((remote/'mock-key-cold-01.zip.sha256').is_file())
        metadata = json.loads((remote/'run.json').read_text())
        self.assertEqual(metadata['automation']['stop_reason'], 'rate_limited')
        self.assertEqual(metadata['automation']['retry_passes'], 0)
        worker.cli = real_cli
        with contextlib.redirect_stdout(io.StringIO()):
            finished = session.run()
        # This is intentionally a one-paper sample of an assigned full range.
        self.assertEqual(finished['stop_reason'], 'finished_with_issues')
        self.assertTrue(finished['terminal'])
        self.assertEqual(finished['retry_passes'], 1)
        events = [json.loads(line) for line in (worker.output_root/'logs/mock-key/events.jsonl').read_text().splitlines()]
        self.assertEqual([r['status'] for r in events if r['step']=='references'], ['failed', 'success'])
