"""Per-task diagnostics explain time without leaking headers or resetting totals."""
import json
from pathlib import Path
import unittest

from arxiv_pipeline.discovery.api import ApiResponse
from arxiv_pipeline.http import RequestScheduler
from arxiv_pipeline.references.api import SemanticScholarClient, ArxivDateClient
from arxiv_pipeline.references.checkpoint import MemoryCheckpointStore
from arxiv_pipeline.references.examples import FakeDates, cited
from arxiv_pipeline.references.service import ReferencesModule
from tests.test_request_scheduler import Clock


class ReferenceHttpDiagnosticsTests(unittest.TestCase):
    task = {'owner': 'trong', 'arxiv_id': '2504.17523'}
    context = {'run_id': 'diagnostics', 'is_mock': True, 'output_root': Path('/tmp/unused-diagnostics')}

    def client(self, kind, clock, replies):
        replies = iter(replies)
        def transport(*args):
            clock.sleep(.25)
            return next(replies)
        kwargs = dict(transport=transport, monotonic=clock.monotonic, sleep=clock.sleep,
                      wall_clock=lambda: clock.now)
        if kind == 's2':
            return SemanticScholarClient(api_key='private-test-key', **kwargs)
        return ArxivDateClient(**kwargs)

    def test_success_keeps_retry_and_wait_deltas_without_sensitive_values(self):
        clock = Clock()
        reader = self.client('s2', clock, [ApiResponse(429, {'Retry-After':'45'}, b''),
            ApiResponse(200, {}, b'{"data":[]}'), ApiResponse(200, {}, b'{"data":[]}')])
        module = ReferencesModule(reader, FakeDates())
        first = module.fetch_references(self.task, self.context)
        p = first['provenance']
        self.assertEqual(p.get('http_s2_attempts'), '2')
        self.assertEqual(p.get('http_s2_429'), '1')
        self.assertEqual(float(p['http_s2_seconds']), .5)
        self.assertEqual(float(p['http_s2_wait_seconds']), 45)
        self.assertNotIn('private-test-key', json.dumps(first))
        second = module.fetch_references(self.task, self.context)
        self.assertEqual(second['provenance']['http_s2_attempts'], '1')
        self.assertEqual(second['provenance']['http_s2_429'], '0')
        self.assertEqual(float(second['provenance']['http_s2_wait_seconds']), 1)

    def test_arxiv_retries_are_separate_and_cached_s2_page_sends_no_new_request(self):
        clock = Clock()
        reader = self.client('s2', clock, [ApiResponse(200, {},
            json.dumps({'data':[cited('2001.00001')]}).encode())])
        dates = self.client('arxiv', clock, [ApiResponse(429, {'Retry-After':'12'}, b''),
            ApiResponse(200, {}, b'<feed xmlns="http://www.w3.org/2005/Atom">'
                b'<entry><id>https://arxiv.org/abs/2001.00001</id>'
                b'<published>2020-01-01</published></entry></feed>')])
        module = ReferencesModule(reader, dates, checkpoints=MemoryCheckpointStore())
        first = module.fetch_references(self.task, self.context)
        p = first['provenance']
        self.assertEqual(first['status'], 'success')
        self.assertEqual(p.get('http_s2_attempts'), '1')
        self.assertEqual(p.get('http_arxiv_attempts'), '2')
        self.assertEqual(p.get('http_arxiv_429'), '1')
        self.assertEqual(float(p['http_arxiv_seconds']), .5)
        self.assertEqual(float(p['http_arxiv_wait_seconds']), 12)
        second = module.fetch_references(self.task, self.context)
        self.assertEqual(second['provenance']['http_s2_attempts'], '0')
        self.assertEqual(second['provenance']['http_arxiv_attempts'], '0')
        self.assertEqual(second['attempts'], 0)

    def test_shared_scheduler_wait_is_measured_on_failed_and_following_tasks(self):
        clock = Clock()
        replies = iter([ApiResponse(429, {'Retry-After':'45'}, b''), ApiResponse(200, {}, b'{"data":[]}')])
        def transport(*args):
            clock.sleep(.25)
            return next(replies)
        scheduler = RequestScheduler(monotonic=clock.monotonic, sleep=clock.sleep, wall_clock=lambda:clock.now)
        # Two clients sharing a scheduler: second must include the queue's cooldown in its own Result.
        kwargs = dict(scheduler=scheduler, transport=transport, max_attempts=1,
                      monotonic=clock.monotonic, sleep=clock.sleep, wall_clock=lambda:clock.now, api_key='')
        first = ReferencesModule(SemanticScholarClient(**kwargs), FakeDates()).fetch_references(self.task, self.context)
        self.assertEqual(first['status'], 'failed')
        self.assertEqual(first['provenance'].get('http_s2_429'), '1')
        second = ReferencesModule(SemanticScholarClient(**kwargs), FakeDates()).fetch_references(self.task, self.context)
        self.assertEqual(float(second['provenance']['http_s2_wait_seconds']), 45)
