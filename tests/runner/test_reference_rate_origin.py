"""Only Semantic Scholar failures may trip the references S2 pause guard."""
from collections import deque
import json
from pathlib import Path
import tempfile
import unittest

from arxiv_pipeline.discovery.api import ApiResponse, RemoteError
from arxiv_pipeline.http import RequestScheduler
from arxiv_pipeline.references.api import ArxivDateClient
from arxiv_pipeline.references.checkpoint import DateCache
from arxiv_pipeline.references.examples import FakeDates, FakeReader, cited, page
from arxiv_pipeline.references.service import ReferencesModule
from arxiv_pipeline.runner.factory import MockModuleFactory
from arxiv_pipeline.runner.models import ModuleSet, RunOptions
from arxiv_pipeline.runner.service import PipelineRunner
from arxiv_pipeline.runner.state import StateStore


def rate_error():
    return RemoteError('rate_limited', 'Synthetic HTTP 429', True, 429)


class DateReplies(FakeDates):
    def __init__(self, replies):
        super().__init__()
        self.replies = deque(replies)

    def submission_dates(self, ids):
        self.calls.append(list(ids))
        self.attempts += 1
        reply = self.replies.popleft()
        if isinstance(reply, Exception):
            raise reply
        return {i: reply[i] for i in ids if i in reply}


class ReferenceFactory(MockModuleFactory):
    def __init__(self, pages, dates, *, cached_dates=None, legacy=False):
        self.pages, self.dates = pages, dates
        self.cached_dates, self.legacy = cached_dates, legacy

    def build(self, options, checkpoints):
        modules = super().build(options, checkpoints)
        if self.cached_dates:
            DateCache(checkpoints).put_many(self.cached_dates)
        refs = ReferencesModule(self.pages, self.dates, checkpoints=checkpoints)
        if self.legacy:
            fetch = refs.fetch_references

            def without_origin(*args):
                value = fetch(*args)
                value['provenance'].pop('error_source', None)
                return value
            refs.fetch_references = without_origin
        return ModuleSet(modules.discovery, modules.metadata, modules.sources, refs)


class ReferenceOriginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def results(self, run_id):
        with StateStore.readonly(self.root/'state.sqlite', run_id) as store:
            rows = store.connection.execute(
                "SELECT result_json FROM attempts WHERE run_id=? AND result_valid=1 "
                "AND json_extract(result_json,'$.step')='references' ORDER BY attempt_id", (run_id,))
            return [json.loads(row['result_json']) for row in rows]

    def test_arxiv_429_does_not_stop_next_paper_even_for_legacy_results(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy), tempfile.TemporaryDirectory() as tmp:
                self.root = Path(tmp)
                factory = ReferenceFactory(
                    FakeReader([page([cited('2001.00001')])] * 3),
                    DateReplies([rate_error(), rate_error(), {'2001.00001': '2020-01-01'}]),
                    legacy=legacy)
                options = RunOptions('origin', self.root, ('trong', 'khang', 'hoang'), True)
                outcome = PipelineRunner(factory).run(options, stage='references',
                    retry_before_attempt=0, references_rate_limit_threshold=2)
                self.assertIsNone(outcome.pause_reason)
                self.assertTrue(outcome.validation['valid'])
                records = self.results('origin')
                self.assertEqual([r['status'] for r in records], ['partial', 'partial', 'success'])
                self.assertTrue(all(r['data']['complete'] for r in records))
                self.assertEqual(records[0]['missing_fields'],
                                 ['/data/references/2001.00001/submission_date'])
                # First-pass failures stay deferred; success is not invented on resume.
                again = PipelineRunner(factory).run(options, stage='references',
                    retry_before_attempt=0, references_rate_limit_threshold=2)
                self.assertEqual((again.executed_tasks, again.deferred_tasks), (0, 2))

    def test_arxiv_date_error_breaks_consecutive_s2_error_streak(self):
        factory = ReferenceFactory(FakeReader([
            rate_error(), page([cited('2001.00001')]), rate_error(), page([])]),
            FakeDates(error=rate_error()))
        outcome = PipelineRunner(factory).run(
            RunOptions('mixed', self.root, ('trong', 'khang', 'hoang', 'man-nhi'), True),
            stage='references', retry_before_attempt=0, references_rate_limit_threshold=2)
        self.assertIsNone(outcome.pause_reason)
        self.assertTrue(outcome.validation['valid'])
        self.assertEqual([r['status'] for r in self.results('mixed')],
                         ['failed', 'partial', 'failed', 'empty'])

    def test_genuine_s2_streak_still_pauses_and_keeps_partial_page(self):
        factory = ReferenceFactory(FakeReader([
            rate_error(), page([cited('2001.00001')], next_offset=1), rate_error()]),
            FakeDates(known={'2001.00001': '2020-01-01'}))
        outcome = PipelineRunner(factory).run(
            RunOptions('s2', self.root, ('trong', 'khang'), True), stage='references',
            retry_before_attempt=0, references_rate_limit_threshold=2)
        self.assertEqual(outcome.pause_reason, 'references_rate_limited')
        self.assertTrue(outcome.validation['valid'])
        records = self.results('s2')
        self.assertEqual([r['status'] for r in records], ['failed', 'partial'])
        self.assertFalse(records[-1]['data']['complete'])
        self.assertEqual(records[-1]['data']['raw_references'], [cited('2001.00001')])

    def test_retry_of_legacy_date_partial_reuses_s2_pages_and_healthy_dates(self):
        options = RunOptions('cached', self.root, ('trong',), True)
        raw = [cited('2001.00001'), cited('2001.00002')]
        factory = ReferenceFactory(FakeReader([page(raw)]), FakeDates(error=rate_error()),
            cached_dates={'2001.00001': '2020-01-01'}, legacy=True)
        first = PipelineRunner(factory).run(options, stage='references',
            retry_before_attempt=0, references_rate_limit_threshold=1)
        self.assertIsNone(first.pause_reason)
        original = self.results('cached')[0]
        with StateStore.readonly(self.root/'state.sqlite', 'cached') as store:
            boundary = store.connection.execute('SELECT max(attempt_id) FROM attempts').fetchone()[0]
        new_dates = FakeDates(known={'2001.00002': '2020-01-02'})
        resumed = PipelineRunner(ReferenceFactory(FakeReader([]), new_dates)).run(options,
            stage='references', retry_before_attempt=boundary, references_rate_limit_threshold=1)
        self.assertEqual(resumed.executed_tasks, 1)
        self.assertTrue(resumed.validation['valid'])
        records = self.results('cached')
        self.assertEqual([r['status'] for r in records], ['partial', 'success'])
        self.assertEqual(records[-1]['data']['raw_references'], original['data']['raw_references'])
        self.assertEqual(new_dates.calls, [['2001.00002']])
        self.assertEqual(records[-1]['provenance']['pages_resumed'], '1')
        self.assertEqual(records[-1]['provenance']['pages_fetched'], '0')
        self.assertEqual(records[-1]['data']['references']['2001.00001']['submission_date'], '2020-01-01')
        self.assertEqual(records[-1]['data']['references']['2001.00002']['submission_date'], '2020-01-02')

    def test_new_results_label_selected_error_origin(self):
        task = {'owner': 'trong', 'arxiv_id': '2504.17523'}
        context = {'run_id': 'origin', 'output_root': self.root, 'is_mock': True}
        scenarios = [
            ([rate_error()], FakeDates(), 'semantic_scholar'),
            ([page([cited('2001.00001')])], FakeDates(error=rate_error()), 'arxiv'),
            ([page([cited('2001.00001')], next_offset=1), rate_error()],
             FakeDates(error=RemoteError('network_error', 'Synthetic timeout', True)), 'semantic_scholar'),
        ]
        for pages, dates, expected in scenarios:
            with self.subTest(expected=expected):
                result = ReferencesModule(FakeReader(pages), dates).fetch_references(task, context)
                self.assertEqual(result['provenance'].get('error_source'), expected)
        healthy = ReferencesModule(FakeReader([page([])]), FakeDates()).fetch_references(task, context)
        self.assertIsNone(healthy['error'])
        self.assertNotIn('error_source', healthy['provenance'])

    def test_arxiv_retry_after_is_still_honored_between_papers(self):
        clock, request_starts = [0.0], []
        def sleep(seconds):
            clock[0] += seconds
        responses = iter([
            ApiResponse(429, {'Retry-After': '120'}, b''),
            ApiResponse(200, {}, b'<feed xmlns="http://www.w3.org/2005/Atom">'
                b'<entry><id>https://arxiv.org/abs/2001.00001v1</id>'
                b'<published>2020-01-01T00:00:00Z</published></entry></feed>')])
        def transport(*args):
            request_starts.append(clock[0])
            return next(responses)
        class Factory(MockModuleFactory):
            def build(self, options, checkpoints):
                modules = super().build(options, checkpoints)
                scheduler = RequestScheduler(checkpoints=checkpoints,
                    monotonic=lambda: clock[0], sleep=sleep, wall_clock=lambda: 1700000000+clock[0])
                dates = ArxivDateClient(scheduler=scheduler, transport=transport, max_attempts=1,
                    monotonic=lambda: clock[0], sleep=sleep, wall_clock=lambda: 1700000000+clock[0])
                refs = ReferencesModule(FakeReader([page([cited('2001.00001')])] * 2),
                                        dates, checkpoints=checkpoints)
                return ModuleSet(modules.discovery, modules.metadata, modules.sources, refs)
        outcome = PipelineRunner(Factory()).run(
            RunOptions('cooldown', self.root, ('trong', 'khang'), True), stage='references',
            retry_before_attempt=0, references_rate_limit_threshold=1)
        self.assertIsNone(outcome.pause_reason)
        self.assertEqual(request_starts, [0, 120])
        self.assertEqual([r['status'] for r in self.results('cooldown')], ['partial', 'success'])
