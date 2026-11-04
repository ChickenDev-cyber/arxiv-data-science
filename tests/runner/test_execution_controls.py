"""Operational pauses preserve completed tasks and resume boundaries."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from arxiv_pipeline.runner.factory import MockModuleFactory, StaticModuleFactory
from arxiv_pipeline.runner.models import RunOptions
from arxiv_pipeline.runner.service import PipelineRunner
from arxiv_pipeline.runner.state import StateStore
from .test_service import Modules


class ExecutionControlsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.options = RunOptions('mock-demo', self.root, ('trong',), True)

    def test_time_limit_finishes_current_task_then_pauses_and_resume_reuses_it(self):
        now = [0.0]
        modules = Modules()
        original = modules.fetch_metadata
        def slow_metadata(*args):
            value = original(*args)
            now[0] += 601
            return value
        with patch.object(modules, 'fetch_metadata', side_effect=slow_metadata):
            runner = PipelineRunner(StaticModuleFactory(modules.bundle), monotonic=lambda: now[0])
            stopped = runner.run(self.options, stop_after_seconds=600, retry_before_attempt=0)
        self.assertEqual(stopped.executed_tasks, 2)
        self.assertEqual(stopped.pause_reason, 'time_limit')
        saved = json.loads((self.root / 'runs/mock-demo/execution.json').read_text())
        self.assertEqual((saved['run_state'], saved['max_attempt_id']), ('paused', 2))
        resumed = runner.run(self.options, stop_after_seconds=600, retry_before_attempt=0)
        self.assertEqual(resumed.executed_tasks, 3)
        self.assertIsNone(resumed.pause_reason)
        self.assertEqual(modules.calls.count('metadata'), 1)
        self.assertIsNone(json.loads((self.root / 'runs/mock-demo/execution.json').read_text())['pause_reason'])

    def test_two_reference_429_results_are_saved_before_pausing_then_other_paper_can_run(self):
        options = RunOptions('mock-demo', self.root, ('trong', 'khang', 'hoang'), True)
        class LimitedFactory(MockModuleFactory):
            def build(self, *args):
                modules = super().build(*args)
                original = modules.references.fetch_references
                def limited(*args):
                    value = original(*args)
                    value.update(status='failed', data=None, error={
                        'code': 'rate_limited', 'message': 'HTTP 429', 'retryable': True, 'http_status': 429})
                    return value
                modules.references.fetch_references = limited
                return modules
        runner = PipelineRunner(LimitedFactory())
        stopped = runner.run(options, stage='references', retry_before_attempt=0,
                             references_rate_limit_threshold=2)
        self.assertEqual(stopped.pause_reason, 'references_rate_limited')
        self.assertEqual(stopped.executed_tasks, 5)  # three discovery + two refs
        with StateStore.readonly(self.root / 'state.sqlite', 'mock-demo') as store:
            self.assertEqual(store.read_run_record().status, 'paused')
        resumed = PipelineRunner(MockModuleFactory()).run(options, stage='references',
            retry_before_attempt=0, references_rate_limit_threshold=2)
        self.assertIsNone(resumed.pause_reason)
        self.assertEqual(resumed.executed_tasks, 1)
        self.assertEqual(resumed.deferred_tasks, 2)

    def test_invalid_controls_fail_before_creating_corpus(self):
        for name, values in {'stop_after_seconds': [0, -1, True, float('nan'), float('inf'), '600'],
                             'references_rate_limit_threshold': [0, -1, True, 1.5]}.items():
            for value in values:
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    PipelineRunner(MockModuleFactory()).run(self.options, **{name: value})
        self.assertFalse((self.root / 'state.sqlite').exists())

    def test_successful_reference_resets_429_streak(self):
        options = RunOptions('mock-demo', self.root, ('trong', 'khang', 'hoang'), True)
        modules = MockModuleFactory().build(options, None)
        original = modules.references.fetch_references
        statuses = iter([429, 200, 429])
        def mixed(*args):
            value = original(*args)
            if next(statuses) == 429:
                value.update(status='failed', data=None, error={
                    'code': 'rate_limited', 'message': 'HTTP 429', 'retryable': True, 'http_status': 429})
            return value
        with patch.object(modules.references, 'fetch_references', side_effect=mixed):
            outcome = PipelineRunner(StaticModuleFactory(modules)).run(options, stage='references',
                references_rate_limit_threshold=2)
        self.assertIsNone(outcome.pause_reason)
        self.assertEqual(outcome.executed_tasks, 6)

    def test_partial_reference_429_keeps_data_and_pauses_even_on_last_paper(self):
        modules = Modules()
        original = modules.fetch_references
        def partial(*args):
            value = original(*args)
            value['status'] = 'partial'
            value['data']['complete'] = False
            value['error'] = {'code': 'rate_limited', 'message': 'HTTP 429',
                              'retryable': True, 'http_status': 429}
            return value
        with patch.object(modules, 'fetch_references', side_effect=partial):
            outcome = PipelineRunner(StaticModuleFactory(modules.bundle)).run(self.options,
                stage='references', references_rate_limit_threshold=1)
        self.assertEqual(outcome.pause_reason, 'references_rate_limited')
        self.assertEqual(outcome.executed_tasks, 2)
        self.assertTrue((self.root/'raw/trong/2504-17523/references.json').is_file())
