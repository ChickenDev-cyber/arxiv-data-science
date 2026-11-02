"""Task execution order across backup pauses and explicit retry passes."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from arxiv_pipeline.runner.models import RunOptions
from arxiv_pipeline.runner.service import run_pipeline
from arxiv_pipeline.runner.state import StateStore
from .test_service import Modules


class RetryPassTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.options = RunOptions('mock-demo', self.root, ('trong',), True)
        self.modules = Modules()

    def run_pass(self, cutoff=0, limit=None):
        return run_pipeline(self.options, self.modules.bundle,
                            retry_before_attempt=cutoff, stop_after_tasks=limit)

    def attempt_count(self):
        with StateStore.readonly(self.root / 'state.sqlite', 'mock-demo') as store:
            return store.connection.execute('SELECT count(*) FROM attempts').fetchone()[0]

    def test_backup_resume_defers_failed_source_until_new_references_are_done(self):
        first = self.run_pass(limit=4)
        self.assertEqual(first.executed_tasks, 4)
        second = self.run_pass()
        self.assertEqual(second.executed_tasks, 1)
        self.assertEqual(second.deferred_tasks, 1)
        self.assertEqual(self.modules.calls.count(('sources', 2)), 1)
        self.assertEqual(self.modules.calls[-1], 'references')
        self.assertEqual(self.attempt_count(), 5)

    def test_failed_task_runs_once_per_retry_pass_across_batch_pauses(self):
        self.run_pass(limit=4)
        # Begin an explicit retry pass before references have run, to exercise a pause.
        boundary = self.attempt_count()
        first_retry_batch = self.run_pass(boundary, limit=1)
        self.assertEqual(first_retry_batch.executed_tasks, 1)
        next_retry_batch = self.run_pass(boundary)
        self.assertEqual(next_retry_batch.executed_tasks, 1)
        self.assertEqual(self.modules.calls.count(('sources', 2)), 2)
        self.assertEqual(self.modules.calls[-1], 'references')
        self.modules.fail_v2 = False
        final = self.run_pass(self.attempt_count())
        self.assertEqual(final.executed_tasks, 1)
        self.assertEqual(final.exit_code, 0)

    def test_failed_metadata_is_deferred_while_new_references_continue(self):
        self.modules.metadata_failure = True
        self.run_pass(limit=2)
        resumed = self.run_pass()
        self.assertEqual(resumed.executed_tasks, 1)
        self.assertEqual(self.modules.calls.count('metadata'), 1)
        self.assertEqual(self.modules.calls[-1], 'references')

    def test_nonretryable_failure_is_not_sent_again_in_retry_pass(self):
        fetch = self.modules.fetch_sources

        def permanent(task, version, context):
            result = fetch(task, version, context)
            if result['error']:
                result['error']['retryable'] = False
            return result

        with patch.object(self.modules, 'fetch_sources', side_effect=permanent):
            self.run_pass()
            retry = self.run_pass(self.attempt_count())
        self.assertEqual(retry.executed_tasks, 0)
        self.assertEqual(self.modules.calls.count(('sources', 2)), 1)

    def test_interrupted_task_without_valid_result_remains_executable(self):
        with patch.object(self.modules, 'fetch_sources', side_effect=KeyboardInterrupt):
            stopped = self.run_pass()
        self.assertEqual(stopped.exit_code, 130)
        resumed = self.run_pass()
        self.assertEqual(resumed.executed_tasks, 3)
        self.assertIn(('sources', 1), self.modules.calls)

    def test_missing_accepted_source_is_repaired_even_in_initial_pass(self):
        self.modules.fail_v2 = False
        self.run_pass()
        (self.root / 'raw/trong/2504-17523/v1/main.tex').unlink()
        repaired = self.run_pass()
        self.assertEqual(repaired.executed_tasks, 1)
        self.assertEqual(repaired.exit_code, 0)
        self.assertEqual(self.modules.calls.count(('sources', 1)), 2)

    def test_invalid_cutoff_fails_before_creating_corpus(self):
        for cutoff in (-1, True, 1.5, '0'):
            with self.subTest(cutoff=cutoff), self.assertRaises(ValueError):
                self.run_pass(cutoff)
        self.assertFalse((self.root / 'state.sqlite').exists())

    def test_future_cutoff_is_rejected_before_any_retry_or_state_transition(self):
        self.run_pass(limit=4)
        calls = list(self.modules.calls)
        count = self.attempt_count()
        with self.assertRaisesRegex(ValueError, 'boundary|cutoff|attempt'):
            self.run_pass(count + 10, limit=1)
        self.assertEqual(self.modules.calls, calls)
        self.assertEqual(self.attempt_count(), count)
        with StateStore.readonly(self.root / 'state.sqlite', 'mock-demo') as store:
            self.assertEqual(store.read_run_record().status, 'paused')

    def test_invalidated_partial_source_is_repaired_after_newer_failure_and_another_pause(self):
        self.modules.fail_v2 = False
        fetch = self.modules.fetch_sources
        phase = [0]
        error = {'code': 'network_error', 'message': 'offline', 'retryable': True, 'http_status': None}

        def partial_then_failed(task, version, context):
            if version == 1 and phase[0] == 1:
                from arxiv_pipeline.examples import result
                failed = result('trong', 'sources', '2504.17523', None, 'failed', 1)
                failed['error'] = dict(error)
                failed['metrics'] = {key: None for key in ('download_bytes', 'extracted_bytes_before_filter',
                    'retained_source_bytes', 'removed_figure_bytes', 'removed_other_bytes')}
                return failed
            value = fetch(task, version, context)
            if version == 1 and phase[0] == 0:
                value['status'] = 'partial'
                value['metrics']['download_bytes'] = None
                value['missing_fields'] = ['/metrics/download_bytes']
                value['error'] = dict(error)
            return value

        with patch.object(self.modules, 'fetch_sources', side_effect=partial_then_failed):
            self.run_pass()
            phase[0] = 1
            boundary = self.attempt_count()
            self.assertEqual(self.run_pass(boundary).executed_tasks, 1)
            source = self.root / 'raw/trong/2504-17523/v1/main.tex'
            source.unlink()
            (self.root / 'raw/trong/2504-17523/metadata.json').unlink()
            phase[0] = 2
            paused = self.run_pass(boundary, limit=1)
            self.assertEqual(paused.executed_tasks, 1)
            with StateStore.readonly(self.root / 'state.sqlite', 'mock-demo') as store:
                self.assertEqual(store.read_run_record().status, 'paused')
            repaired = self.run_pass(boundary)
        self.assertEqual(repaired.executed_tasks, 1)
        self.assertEqual(repaired.exit_code, 0)
        self.assertTrue(source.is_file())
