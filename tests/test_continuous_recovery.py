"""Automatic rate recovery must preserve the existing journal and backup order."""
import contextlib
import importlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from tests.test_colab_automation import notebook_namespace
from tests.test_colab_automation_controller import FakeBackend
from tests.test_request_scheduler import Clock


class ContinuousRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ns = notebook_namespace()
        self.clock = Clock()
        self.events = []
        self.module = importlib.import_module('arxiv_pipeline.runner.continuous')

    def report(self, limited=False):
        return {'run_state': 'paused' if limited else 'finished',
                'pause_reason': 'references_rate_limited' if limited else None,
                'max_attempt_id': 10, 'retryable_tasks': 0, 'progress_key': 'progress',
                'summary': {}, 'scope_complete': True, 'issues': []}

    def session(self, backend, **kwargs):
        return self.ns['AutoCrawlSession'](backend, owner='trong', run_id='continuous-test',
            pipeline_commit='f'*40, journal_path=self.root/'session.json', **kwargs)

    def run_auto(self, session, **kwargs):
        kwargs.setdefault('enabled', True)
        kwargs.setdefault('monotonic', self.clock.monotonic)
        kwargs.setdefault('sleep', self.clock.sleep)
        kwargs.setdefault('record_event', self.events.append)
        with contextlib.redirect_stdout(io.StringIO()):
            return self.module.run_continuously(session, **kwargs)

    def test_preflight_429_waits_then_runs_without_consuming_data_retry_budget(self):
        backend = FakeBackend(step_responses=[self.report()])
        starts = []
        def preflight():
            starts.append(self.clock.now)
            if len(starts) == 1:
                raise self.module.RateLimitPause(wait_seconds=120)
        backend.preflight = preflight
        result = self.run_auto(self.session(backend))
        self.assertEqual(starts, [0, 120])
        self.assertEqual((result['batches'], result['retry_passes']), (1, 0))
        self.assertEqual(backend.batch_boundaries, [0])
        self.assertTrue(result['terminal'])

    def test_batch_rate_pause_is_backed_up_before_sleep_and_resume(self):
        backend = FakeBackend(step_responses=[self.report(True), self.report()])
        timeline = []
        original_backup = backend.backup_batch
        def backup(*args):
            original_backup(*args)
            timeline.append('backup')
        backend.backup_batch = backup
        def sleep(seconds):
            timeline.append('sleep')
            self.clock.sleep(seconds)
        result = self.run_auto(self.session(backend), sleep=sleep)
        self.assertEqual(timeline[0], 'backup')
        self.assertEqual(result['batches'], 2)
        self.assertEqual(backend.batch_boundaries, [0, 0])
        self.assertEqual(result['retry_passes'], 0)
        self.assertEqual(self.clock.now, 60)

    def test_preflight_permissions_and_unknown_errors_never_retry_or_leak_error_text(self):
        for error in (RuntimeError('401 upstream-secret'), ValueError('bad state upstream-secret')):
            with self.subTest(error=type(error).__name__):
                backend = FakeBackend()
                def fail():
                    raise error
                backend.preflight = fail
                with self.assertRaises(type(error)):
                    self.run_auto(self.session(backend))
                self.assertEqual(self.clock.now, 0)
                self.assertFalse(any(call[0] == 'execute_batch' for call in backend.calls))
                self.assertNotIn('upstream-secret', json.dumps(self.events))

    def test_backup_failure_including_typed_pause_never_enters_retry_loop(self):
        for error in (RuntimeError('Drive checksum failed'), self.module.RateLimitPause()):
            with self.subTest(error=type(error).__name__):
                # A separate journal keeps each pending-backup scenario independent.
                (self.root/'session.json').unlink(missing_ok=True)
                backend = FakeBackend(step_responses=[self.report(True)])
                def fail(*args):
                    raise error
                backend.backup_batch = fail
                with self.assertRaises(type(error)):
                    self.run_auto(self.session(backend))
                self.assertEqual(self.clock.now, 0)
                self.assertEqual(len([c for c in backend.calls if c[0] == 'execute_batch']), 1)
                self.assertIsNotNone(json.loads((self.root/'session.json').read_text())['pending'])

    def test_recovery_count_exhaustion_has_explicit_nonterminal_result(self):
        backend = FakeBackend()
        starts = []
        def limited():
            starts.append(self.clock.now)
            raise self.module.RateLimitPause()
        backend.preflight = limited
        result = self.run_auto(self.session(backend), max_recoveries=2)
        self.assertEqual(starts, [0, 60, 180])
        self.assertEqual(result['stop_reason'], 'rate_limit_recovery_exhausted')
        self.assertFalse(result['terminal'])
        self.assertEqual(result['recovery']['recoveries'], 2)
        self.assertEqual(result['recovery']['wait_seconds'], 180)

    def test_wait_budget_stops_before_next_retry_without_shortening_cooldown(self):
        backend = FakeBackend()
        def limited():
            raise self.module.RateLimitPause()
        backend.preflight = limited
        result = self.run_auto(self.session(backend), max_wait_seconds=100)
        self.assertEqual(result['stop_reason'], 'rate_limit_wait_budget_exhausted')
        self.assertEqual(self.clock.now, 60)
        self.assertEqual(result['recovery']['recoveries'], 1)

    def test_server_deadline_is_audited_even_when_it_exceeds_this_calls_wait_budget(self):
        backend = FakeBackend()
        def limited():
            raise self.module.RateLimitPause(wait_seconds=3600)
        backend.preflight = limited
        result = self.run_auto(self.session(backend), max_wait_seconds=100)
        self.assertEqual(result['stop_reason'], 'rate_limit_wait_budget_exhausted')
        observed = [e for e in self.events if e['event'] == 'rate_limit_pause_observed']
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0]['requested_wait_seconds'], 3600)
        self.assertEqual(self.clock.now, 0)

    def test_long_persisted_cooldown_is_not_capped_at_fifteen_minutes(self):
        backend = FakeBackend(step_responses=[self.report(True), self.report()])
        backend.cooldown_seconds = lambda: 10000
        result = self.run_auto(self.session(backend))
        self.assertTrue(result['terminal'])
        self.assertEqual(self.clock.now, 10000)

    def test_successful_recheck_at_exact_wait_budget_can_continue_crawling(self):
        backend = FakeBackend(step_responses=[self.report(True), self.report()])
        result = self.run_auto(self.session(backend), max_wait_seconds=60)
        self.assertTrue(result['terminal'])
        self.assertEqual(result['batches'], 2)
        self.assertEqual(self.clock.now, 60)

    def test_actual_oversleep_beyond_budget_stops_before_another_probe_or_batch(self):
        backend = FakeBackend(step_responses=[self.report(True), self.report()])
        def oversleep(seconds):
            self.clock.sleep(seconds + 1)
        result = self.run_auto(self.session(backend), max_wait_seconds=60, sleep=oversleep)
        self.assertEqual(result['stop_reason'], 'rate_limit_wait_budget_exhausted')
        self.assertEqual(len([c for c in backend.calls if c[0] == 'execute_batch']), 1)

    def test_restored_cumulative_counters_do_not_consume_the_one_batch_cap(self):
        backend = FakeBackend(step_responses=[self.report()])
        starts = []
        def preflight():
            starts.append(self.clock.now)
            if len(starts) == 1:
                raise self.module.RateLimitPause()
        backend.preflight = preflight
        session = self.session(backend, resume_state={'owner': 'trong', 'run_id': 'continuous-test',
            'pipeline_commit': 'f'*40, 'mode': 'resume', 'next_measurement': 'resume-35',
            'batches': 34, 'retry_passes': 1, 'retry_before_attempt': 12})
        result = self.run_auto(session, max_batches_this_call=1)
        self.assertEqual(result['batches'], 35)
        self.assertEqual(result['retry_passes'], 1)
        self.assertEqual(backend.batch_boundaries, [12])
        self.assertEqual(len([c for c in backend.calls if c[0] == 'execute_batch']), 1)

    def test_one_batch_cap_is_not_reset_by_outer_recovery(self):
        backend = FakeBackend(step_responses=[self.report(True), self.report()])
        result = self.run_auto(self.session(backend), max_batches_this_call=1)
        self.assertEqual(result['batches'], 1)
        self.assertEqual(result['stop_reason'], 'rate_limited')
        self.assertEqual(self.clock.now, 0)
        self.assertEqual(len([c for c in backend.calls if c[0] == 'execute_batch']), 1)

    def test_cumulative_batch_limit_stays_terminal(self):
        backend = FakeBackend(step_responses=[self.report(True)])
        result = self.run_auto(self.session(backend, max_batches=1))
        self.assertTrue(result['terminal'])
        self.assertEqual(result['stop_reason'], 'batch_limit')
        self.assertEqual(self.clock.now, 0)

    def test_ctrl_c_during_wait_does_not_start_another_batch(self):
        backend = FakeBackend(step_responses=[self.report(True), self.report()])
        def interrupt(seconds):
            self.clock.sleep(5)
            raise KeyboardInterrupt()
        result = self.run_auto(self.session(backend), sleep=interrupt)
        self.assertEqual(result['stop_reason'], 'interrupted')
        self.assertFalse(result['terminal'])
        self.assertEqual(result['recovery']['wait_seconds'], 5)
        self.assertEqual(len([c for c in backend.calls if c[0] == 'execute_batch']), 1)
        self.assertIsNone(json.loads((self.root/'session.json').read_text())['pending'])

    def test_disabled_mode_preserves_existing_rate_limit_stop(self):
        backend = FakeBackend(step_responses=[self.report(True), self.report()])
        result = self.run_auto(self.session(backend), enabled=False)
        self.assertEqual(result['stop_reason'], 'rate_limited')
        self.assertEqual(result['batches'], 1)
        self.assertEqual(self.clock.now, 0)

    def test_second_continuous_controller_is_blocked_during_cooldown(self):
        backend = FakeBackend(step_responses=[self.report(True), self.report()])
        first = self.session(backend)
        def sleep(seconds):
            with self.assertRaisesRegex(RuntimeError, 'continuous|liên tục'):
                self.run_auto(self.session(backend))
            self.clock.sleep(seconds)
        result = self.run_auto(first, sleep=sleep)
        self.assertTrue(result['terminal'])

    def test_audit_failure_stops_before_api_or_batch(self):
        backend = FakeBackend()
        def fail(event):
            raise OSError('Drive unavailable')
        with self.assertRaises(OSError):
            self.run_auto(self.session(backend), record_event=fail)
        self.assertEqual(backend.calls, [])

    def test_invalid_policy_stops_before_any_backend_call(self):
        for options in ({'enabled': 1}, {'max_recoveries': True}, {'max_recoveries': 0},
                        {'max_wait_seconds': float('inf')}, {'max_wait_seconds': -1},
                        {'max_batches_this_call': 0}):
            with self.subTest(options=options):
                backend = FakeBackend()
                with self.assertRaises(ValueError):
                    self.run_auto(self.session(backend), **options)
                self.assertEqual(backend.calls, [])
