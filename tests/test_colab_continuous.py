"""Exercise real notebook controls, immutable audit copies and measured recovery."""
import contextlib
import io
import json
from pathlib import Path
import shlex
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from arxiv_pipeline.http import checkpoint_record, canonical_checkpoint_key, utc_iso
from arxiv_pipeline.runner.continuous import RateLimitPause, run_continuously
from arxiv_pipeline.runner.state import StateStore
from tests.test_colab_automation import REPO, notebook_namespace
from tests.test_request_scheduler import Clock


class ColabContinuousTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ns = notebook_namespace()

    def worker(self, **kwargs):
        return self.ns['NotebookBatchWorker'](repo_dir=REPO, env=self.ns['PIPELINE_ENV'],
            work_root=self.root, output_root=self.root/'corpus', drive_root=self.root,
            owner='trong', run_id='mock-continuous', pipeline_commit='f'*40,
            expected_mock=True, stop_after_tasks=50,
            initial_args=['--mock', '--owner', 'trong', '--max-candidates', '1', '--max-papers', '1'], **kwargs)

    def test_notebook_loader_rejects_a_kernel_with_another_pipeline_checkout(self):
        self.assertTrue(callable(self.ns.get('load_continuous_controls')))
        foreign = SimpleNamespace(__file__='/tmp/another-checkout/src/arxiv_pipeline/__init__.py')
        with patch.dict('sys.modules', {'arxiv_pipeline': foreign}), \
             self.assertRaisesRegex(RuntimeError, 'Kernel'):
            self.ns['load_continuous_controls'](REPO)

    def test_only_trusted_rate_limit_preflight_results_become_typed_pauses(self):
        worker = self.worker(require_s2_key=True)
        worker.expected_mock = False
        worker.env['S2_API_KEY'] = 'offline-secret'
        for probe in ({'ok': False, 'code': 'rate_limited', 'http_status': 429, 'wait_seconds': 3600},
                      {'ok': False, 'code': 'cooldown', 'http_status': None, 'wait_seconds': 180}):
            with self.subTest(probe=probe), patch.object(worker, '_capture', return_value=probe), \
                 contextlib.redirect_stdout(io.StringIO()) as out:
                with self.assertRaises(RateLimitPause) as caught:
                    worker.preflight()
                self.assertEqual(caught.exception.wait_seconds, probe['wait_seconds'])
                self.assertNotIn('offline-secret', str(caught.exception) + out.getvalue())
        for status in (401, 403, 503):
            with self.subTest(status=status), patch.object(worker, '_capture', return_value={
                    'ok': False, 'code': 'remote_error', 'http_status': status}):
                with self.assertRaises(RuntimeError) as caught:
                    worker.preflight()
                self.assertNotIsInstance(caught.exception, RateLimitPause)

    def test_cooldown_accessor_uses_saved_state_without_key_or_http(self):
        worker = self.worker()
        self.assertTrue(hasattr(worker, 'cooldown_seconds'), 'Worker needs read-only saved cooldown access')
        with contextlib.redirect_stdout(io.StringIO()):
            batch = worker.make_batch('pilot', 'cold-01')
            worker.execute_batch(batch)
        with StateStore(worker.output_root/'state.sqlite', worker.run_id) as store:
            store.save_checkpoint(checkpoint_record('rate_limits', canonical_checkpoint_key('semantic_scholar'),
                {'group': 'semantic_scholar', 'next_allowed_at': utc_iso(time.time()+3600),
                 'recent_requests_at': [], 'consecutive_429': 2}))
        before = (worker.output_root/'state.sqlite').read_bytes()
        worker.env.pop('S2_API_KEY', None)
        self.assertGreater(worker.cooldown_seconds(), 3500)
        self.assertEqual((worker.output_root/'state.sqlite').read_bytes(), before)

    def test_new_vm_respects_mirrored_wait_even_if_snapshot_predates_last_probe(self):
        worker = self.worker(require_s2_key=True)
        worker.expected_mock = False
        worker.record_control_event({'event': 'auto_run_started', 'elapsed_seconds': 0})
        worker.record_control_event({'event': 'rate_limit_wait_started', 'requested_wait_seconds': 3600})
        resumed = self.worker(require_s2_key=True)
        resumed.expected_mock = False
        resumed.env['S2_API_KEY'] = 'offline-secret'
        with patch.object(resumed, '_capture', side_effect=AssertionError('No HTTP before saved deadline')), \
             self.assertRaises(RateLimitPause) as caught:
            resumed.preflight()
        self.assertEqual(caught.exception.code, 'cooldown')
        self.assertGreater(caught.exception.wait_seconds, 3500)

    def test_new_vm_respects_deadline_recorded_without_starting_a_wait(self):
        worker = self.worker(require_s2_key=True)
        worker.expected_mock = False
        with self.assertRaises(ValueError):
            worker.record_control_event({'event': 'rate_limit_pause_observed', 'requested_wait_seconds': 7200})
        worker.record_control_event({'event': 'auto_run_started', 'elapsed_seconds': 0})
        worker.record_control_event({'event': 'rate_limit_pause_observed', 'requested_wait_seconds': 7200})
        self.assertGreater(worker.cooldown_seconds(), 7100)

    def test_control_events_are_immutable_and_mirrored_with_safe_identity(self):
        worker = self.worker()
        self.assertTrue(hasattr(worker, 'record_control_event'), 'Need durable control event recording')
        worker.record_control_event({'event': 'auto_run_started', 'elapsed_seconds': 0})
        worker.record_control_event({'event': 'rate_limit_wait_started', 'requested_wait_seconds': 60})
        local = sorted((self.root/'control-events'/worker.run_id).glob('*/*.json'))
        remote = sorted((self.root/'trong'/worker.run_id/'control-events').glob('*/*.json'))
        self.assertEqual(len(local), 2)
        self.assertEqual([p.read_bytes() for p in local], [p.read_bytes() for p in remote])
        record = json.loads(local[1].read_text())
        self.assertEqual((record['owner'], record['run_id'], record['pipeline_commit']),
                         ('trong', worker.run_id, 'f'*40))
        first = local[0].read_bytes()
        worker.record_control_event({'event': 'auto_run_finished', 'wall_seconds': 65})
        self.assertEqual(local[0].read_bytes(), first)
        with self.assertRaises(ValueError):
            worker.record_control_event({'event': 'auto_run_started', 'S2_API_KEY': 'must-not-write'})
        self.assertNotIn('must-not-write', ''.join(p.read_text() for p in local))

    def test_audit_mirror_failure_prevents_a_new_measured_batch(self):
        worker = self.worker()
        self.assertTrue(hasattr(worker, 'record_control_event'))
        session = self.ns['AutoCrawlSession'](worker, owner=worker.owner, run_id=worker.run_id,
            pipeline_commit=worker.pipeline_commit, journal_path=self.root/'session.json', initial_mode='pilot')
        def fail(*args, **kwargs):
            raise OSError('Drive is unavailable')
        with patch.dict(self.ns, {'copy_verified': fail}), self.assertRaises(OSError):
            run_continuously(session, enabled=True, record_event=worker.record_control_event)
        self.assertFalse((worker.output_root/'state.sqlite').exists())

    def test_cold_preflight_long_retry_after_is_mirrored_without_short_early_retry(self):
        from arxiv_pipeline.discovery.api import ApiResponse
        from arxiv_pipeline.references.preflight import main as probe_main
        worker = self.worker(require_s2_key=True)
        worker.expected_mock = False
        worker.env['S2_API_KEY'] = 'offline-secret'
        original_capture = worker._capture
        sends = []
        def limited(*args):
            sends.append(args)
            return ApiResponse(429, {'Retry-After': '3600'}, b'')
        def capture(args, **kwargs):
            if 'arxiv_pipeline.references.preflight' not in args:
                return original_capture(args, **kwargs)
            with patch.dict('os.environ', {'S2_API_KEY': 'offline-secret'}), \
                 patch('arxiv_pipeline.references.api._semantic_scholar_transport', limited), \
                 patch('socket.create_connection', side_effect=AssertionError('No real network')), \
                 contextlib.redirect_stdout(io.StringIO()) as out:
                probe_main(args[3:])
            return json.loads(out.getvalue())
        session = self.ns['AutoCrawlSession'](worker, owner=worker.owner, run_id=worker.run_id,
            pipeline_commit=worker.pipeline_commit, journal_path=self.root/'session.json', initial_mode='pilot')
        clock = Clock()
        with patch.object(worker, '_capture', side_effect=capture), contextlib.redirect_stdout(io.StringIO()):
            result = run_continuously(session, enabled=True, max_wait_seconds=100,
                monotonic=clock.monotonic, sleep=clock.sleep, record_event=worker.record_control_event)
        self.assertEqual(result['stop_reason'], 'rate_limit_wait_budget_exhausted')
        self.assertEqual(clock.now, 0)
        self.assertEqual(len(sends), 1)
        self.assertFalse(worker.output_root.exists())
        self.assertGreater(worker.cooldown_seconds(), 3500)

    def test_real_measured_pause_is_backed_up_then_recovery_finishes_same_data_passes(self):
        worker = self.worker(references_rate_limit_threshold=1)
        self.assertTrue(hasattr(worker, 'record_control_event'))
        real_cli = worker.cli
        first = True
        adapter = '''import sys
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
    result.update(status='failed',data=None,error={'code':'rate_limited','message':'offline429','retryable':True,'http_status':429})
    return result
  return ModuleSet(modules.discovery,modules.metadata,modules.sources,References())
cli.MockModuleFactory=Factory
raise SystemExit(cli.main(sys.argv[1:]))
'''
        def limited_once(args, **kwargs):
            nonlocal first
            args = list(args)
            if first and 'arxiv_pipeline.benchmark' in args:
                first = False
                index = args.index('--cmd')+1
                runner = shlex.split(args[index])
                args[index] = shlex.join([runner[0], '-c', adapter, *runner[3:]])
            return real_cli(args, **kwargs)
        worker.cli = limited_once
        session = self.ns['AutoCrawlSession'](worker, owner=worker.owner, run_id=worker.run_id,
            pipeline_commit=worker.pipeline_commit, journal_path=self.root/'session.json', initial_mode='pilot')
        clock = Clock()
        def sleep(seconds):
            # At the first sleep, failed batch already has its verified recovery point.
            self.assertTrue((self.root/'trong'/worker.run_id/'cold-01'/
                             f'{worker.run_id}-cold-01.zip.sha256').is_file())
            clock.sleep(seconds)
        with contextlib.redirect_stdout(io.StringIO()):
            result = run_continuously(session, enabled=True, monotonic=clock.monotonic,
                sleep=sleep, record_event=worker.record_control_event)
        self.assertTrue(result['terminal'])
        self.assertEqual(result['retry_passes'], 1)  # Failed task retried after the first traversal.
        self.assertEqual(clock.now, 60)
        events = [json.loads(line) for line in (worker.output_root/f'logs/{worker.run_id}/events.jsonl').read_text().splitlines()]
        self.assertEqual([e['status'] for e in events if e['step']=='references'], ['failed', 'success'])
        control = sorted((self.root/'trong'/worker.run_id/'control-events').glob('*/*.json'))
        final = json.loads(control[-1].read_text())
        self.assertEqual(final['event'], 'auto_run_finished')
        self.assertEqual(final['actual_wait_seconds'], 60)
