"""Rate limits wait within a bounded runner without losing published work."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from arxiv_pipeline.discovery.api import ApiResponse
from arxiv_pipeline.http import RequestScheduler, canonical_checkpoint_key, parse_utc_iso
from arxiv_pipeline.references.api import SemanticScholarClient
from arxiv_pipeline.references.examples import FakeDates
from arxiv_pipeline.references.service import ReferencesModule
from arxiv_pipeline.runner.factory import LiveModuleFactory, MockModuleFactory, StaticModuleFactory
from arxiv_pipeline.runner.models import ModuleSet, RunOptions
from arxiv_pipeline.runner.service import PipelineRunner
from arxiv_pipeline.runner.state import StateStore, load_options
from arxiv_pipeline.runner.checkpoints import InMemoryCheckpointStore, SQLiteCheckpointStore
from arxiv_pipeline.runner.waiting import BudgetedWait, WaitBudgetExpired
from arxiv_pipeline.collection.inspector import RunInspector
from arxiv_pipeline.runner.restore import RunStateRelocator
from arxiv_pipeline.snapshots.service import SnapshotService
from tests.test_request_scheduler import Clock


class NetworkFactory(MockModuleFactory):
    def __init__(self, clock, replies):
        self.clock, self.replies = clock, iter(replies)
        self.starts = []
        self.wall_base = time.time()

    def build(self, options, checkpoints, *, waiter=None):
        modules = super().build(options, checkpoints)
        clock = self.clock
        scheduler = RequestScheduler(checkpoints=checkpoints, monotonic=clock.monotonic,
            sleep=clock.sleep, wall_clock=lambda: self.wall_base+clock.now, waiter=waiter)
        def transport(*args):
            self.starts.append(clock.now)
            return next(self.replies)
        reader = SemanticScholarClient(api_key='synthetic-key', scheduler=scheduler,
            transport=transport, monotonic=clock.monotonic, sleep=clock.sleep,
            wall_clock=lambda: self.wall_base+clock.now, waiter=waiter)
        refs = ReferencesModule(reader, FakeDates(), checkpoints=checkpoints)
        return ModuleSet(modules.discovery, modules.metadata, modules.sources, refs)


class InRunWaitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'corpus'
        self.clock = Clock()

    def runner(self, factory):
        return PipelineRunner(factory, monotonic=self.clock.monotonic, sleep=self.clock.sleep)

    def test_two_rate_limited_tasks_then_healthy_task_finish_in_one_run(self):
        factory = NetworkFactory(self.clock,
            [ApiResponse(429, {}, b'')]*6 + [ApiResponse(200, {}, b'{"data":[]}')])
        outcome = self.runner(factory).run(
            RunOptions('wait', self.root, ('trong', 'khang', 'hoang'), True),
            stage='references', stop_after_seconds=1800, retry_before_attempt=0,
            references_rate_limit_threshold=2, references_rate_limit_mode='wait')
        self.assertIsNone(outcome.pause_reason)
        self.assertEqual(outcome.executed_tasks, 6)  # three discoveries, three references
        self.assertEqual(factory.starts, [0, 30, 90, 210, 330, 450, 570])
        self.assertTrue(outcome.validation['valid'])
        with StateStore.readonly(self.root/'state.sqlite', 'wait') as store:
            records = [json.loads(r['result_json']) for r in store.connection.execute(
                "SELECT result_json FROM attempts WHERE result_valid=1 "
                "AND json_extract(result_json,'$.step')='references' ORDER BY attempt_id")]
            self.assertEqual([r['status'] for r in records], ['failed', 'failed', 'empty'])
            self.assertEqual([r['attempts'] for r in records], [3, 3, 1])

    def test_deadline_stops_before_retry_and_resume_honors_saved_retry_after(self):
        factory = NetworkFactory(self.clock,
            [ApiResponse(429, {'Retry-After': '120'}, b''), ApiResponse(200, {}, b'{"data":[]}')])
        options = RunOptions('deadline', self.root, ('trong',), True)
        first = self.runner(factory).run(options, stage='references', stop_after_seconds=60,
            retry_before_attempt=0, references_rate_limit_threshold=2, references_rate_limit_mode='wait')
        self.assertEqual(first.pause_reason, 'time_limit')
        self.assertEqual(factory.starts, [0])
        self.assertEqual(self.clock.now, 60)
        self.assertTrue(first.validation['valid'])
        with StateStore.readonly(self.root/'state.sqlite', 'deadline') as store:
            self.assertEqual(store.read_run_record().status, 'paused')
            deadline = store.load_checkpoint('rate_limits', canonical_checkpoint_key('semantic_scholar'))
            remaining = parse_utc_iso(deadline['payload']['next_allowed_at']) - (factory.wall_base+self.clock.now)
            self.assertAlmostEqual(remaining, 60, places=4)
        resumed = self.runner(factory).run(options, stage='references', stop_after_seconds=1800,
            retry_before_attempt=0, references_rate_limit_threshold=2, references_rate_limit_mode='wait')
        self.assertIsNone(resumed.pause_reason)
        self.assertEqual(factory.starts[0], 0)
        self.assertAlmostEqual(factory.starts[1], 120, places=4)  # persisted UTC has microsecond precision
        self.assertEqual(resumed.executed_tasks, 1)

    def test_deadline_during_pagination_preserves_page_for_next_invocation(self):
        item = {'citedPaper': {'paperId': 's2-non-arxiv', 'externalIds': {'DOI': '10.1/example'}}}
        factory = NetworkFactory(self.clock, [
            ApiResponse(200, {}, json.dumps({'data': [item], 'next': 1}).encode()),
            ApiResponse(429, {'Retry-After': '120'}, b''),
            ApiResponse(200, {}, b'{"data":[]}')])
        options = RunOptions('pages', self.root, ('trong',), True)
        first = self.runner(factory).run(options, stage='references', stop_after_seconds=60,
            references_rate_limit_mode='wait')
        self.assertEqual(first.pause_reason, 'time_limit')
        self.assertEqual(factory.starts, [0, 1])
        snapshot = SnapshotService(RunInspector(), RunStateRelocator())
        archive = Path(self.temp.name)/'paused.tar.gz'
        snapshot.export(self.root, 'pages', archive, snapshot_id='paused-pages', exporter_commit='f'*40)
        destination = Path(self.temp.name)/'restored'
        snapshot.restore(archive, destination)
        self.root = destination/'corpus'
        options = load_options(self.root/'state.sqlite', 'pages')
        resumed = self.runner(factory).run(options, stage='references', stop_after_seconds=1800,
            references_rate_limit_mode='wait')
        self.assertTrue(resumed.validation['valid'])
        with StateStore.readonly(self.root/'state.sqlite', 'pages') as store:
            row = store.connection.execute("SELECT result_json FROM attempts WHERE result_valid=1 "
                "AND json_extract(result_json,'$.step')='references'").fetchone()
            result = json.loads(row[0])
            self.assertEqual(result['data']['raw_references'], [item])
            self.assertEqual(result['provenance']['pages_resumed'], '1')
            self.assertEqual(result['attempts'], 1)

    def test_interrupt_while_waiting_produces_resumable_checkpoint(self):
        factory = NetworkFactory(self.clock, [ApiResponse(429, {}, b''),
                                             ApiResponse(200, {}, b'{"data":[]}')])
        def interrupt(_):
            raise KeyboardInterrupt()
        runner = PipelineRunner(factory, monotonic=self.clock.monotonic, sleep=interrupt)
        options = RunOptions('interrupt', self.root, ('trong',), True)
        first = runner.run(options, stage='references', stop_after_seconds=1800,
                           references_rate_limit_mode='wait')
        self.assertEqual((first.exit_code, first.pause_reason), (130, 'interrupted'))
        self.assertTrue(first.validation['valid'])
        resumed = self.runner(factory).run(options, stage='references', stop_after_seconds=1800,
                                         references_rate_limit_mode='wait')
        self.assertIsNone(resumed.pause_reason)
        self.assertEqual(factory.starts[0], 0)
        self.assertAlmostEqual(factory.starts[1], 30, places=4)

    def test_persist_crossing_deadline_sends_no_request_and_can_resume(self):
        clock = self.clock
        class SlowStore(InMemoryCheckpointStore):
            def save(inner, record):
                clock.sleep(2)
                super().save(record)
        store = SlowStore()
        waiter = BudgetedWait(monotonic=clock.monotonic, sleep=clock.sleep)
        waiter.deadline = 1
        scheduler = RequestScheduler(store=store, monotonic=clock.monotonic,
            sleep=clock.sleep, wall_clock=lambda: clock.now, waiter=waiter)
        sent = []
        def transport(*args):
            sent.append(clock.now)
            return ApiResponse(200, {}, b'{"data":[]}')
        reader = SemanticScholarClient(api_key='', scheduler=scheduler, waiter=waiter,
            transport=transport, monotonic=clock.monotonic, sleep=clock.sleep)
        with self.assertRaises(WaitBudgetExpired):
            reader.references_page('2504.17523', 0, 100)
        self.assertEqual(sent, [])
        self.assertEqual(reader.attempts, 0)
        self.assertEqual(reader.http_diagnostics()['attempts'], 0)
        # The conservative slot reservation remains valid across restoration.
        waiter.deadline = 60
        resumed = RequestScheduler(store=store, monotonic=clock.monotonic,
            sleep=clock.sleep, wall_clock=lambda: clock.now, waiter=waiter)
        reader = SemanticScholarClient(api_key='', scheduler=resumed, waiter=waiter,
            transport=transport, monotonic=clock.monotonic, sleep=clock.sleep)
        self.assertEqual(reader.references_page('2504.17523', 0, 100), {'data': []})
        self.assertEqual(sent, [4])
        self.assertEqual(reader.attempts, 1)

    def test_slow_persistence_deadline_checkpoints_runner_before_transport(self):
        factory = NetworkFactory(self.clock, [ApiResponse(200, {}, b'{"data":[]}')])
        options = RunOptions('slow-persist', self.root, ('trong',), True)
        original_save = SQLiteCheckpointStore.save
        clock = self.clock
        def save(store, record):
            if record['namespace'] == 'rate_limits':
                clock.sleep(2)
            return original_save(store, record)
        with patch.object(SQLiteCheckpointStore, 'save', save):
            first = self.runner(factory).run(options, stage='references',
                references_rate_limit_mode='wait', stop_after_seconds=1)
        self.assertEqual(first.pause_reason, 'time_limit')
        self.assertEqual(factory.starts, [])
        self.assertTrue(first.validation['valid'])
        resumed = self.runner(factory).run(options, stage='references',
            references_rate_limit_mode='wait', stop_after_seconds=60)
        self.assertIsNone(resumed.pause_reason)
        self.assertEqual(factory.starts, [2])
        self.assertTrue(resumed.validation['valid'])

    def test_injected_scheduler_wait_is_rejected_before_mutating_new_or_existing_corpus(self):
        options = RunOptions('injected', self.root, ('trong',), True)
        factory = LiveModuleFactory(scheduler=RequestScheduler())
        with self.assertRaisesRegex(ValueError, 'factory-owned scheduler'):
            self.runner(factory).run(options, references_rate_limit_mode='wait', stop_after_seconds=60)
        self.assertFalse(self.root.exists())
        # Existing corpus state, including accepted Results and run status, is untouched too.
        self.runner(MockModuleFactory()).run(options)
        before = (self.root/'state.sqlite').read_bytes()
        with self.assertRaisesRegex(ValueError, 'factory-owned scheduler'):
            self.runner(factory).run(options, references_rate_limit_mode='wait', stop_after_seconds=60)
        self.assertEqual((self.root/'state.sqlite').read_bytes(), before)

    def test_wait_mode_rejects_invalid_or_unbounded_configuration_before_creating_corpus(self):
        options = RunOptions('invalid', self.root, ('trong',), True)
        for kwargs in ({'references_rate_limit_mode': 'unknown'},
                       {'references_rate_limit_mode': 'wait'},
                       {'references_rate_limit_mode': 'wait', 'stop_after_seconds': 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.runner(MockModuleFactory()).run(options, **kwargs)
            self.assertFalse(self.root.exists())
        modules = MockModuleFactory().build(options, None)
        with self.assertRaisesRegex(ValueError, 'factory'):
            self.runner(StaticModuleFactory(modules)).run(options,
                references_rate_limit_mode='wait', stop_after_seconds=60)
        self.assertFalse(self.root.exists())
