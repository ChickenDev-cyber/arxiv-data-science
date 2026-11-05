"""Offline cross-paper arXiv dates tests (no network or real corpus)."""
import unittest
from pathlib import Path
from arxiv_pipeline.discovery.api import RemoteError
from arxiv_pipeline.references.service import ReferencesModule
from arxiv_pipeline.references.examples import FakeDates, FakeReader, cited, page
from arxiv_pipeline.validation import validate_for_task
from arxiv_pipeline.runner.models import paper_spec

TASKS = [
    {'owner': 'khang', 'arxiv_id': '2506.20788'},
    {'owner': 'khang', 'arxiv_id': '2506.20789'},
]
CTX = {'run_id': 'offline-batch', 'is_mock': True, 'output_root': Path('/tmp/unused')}
DATE = '2020-01-01T00:00:00Z'


class ReferenceBatchTests(unittest.TestCase):
    def test_shared_cited_id_uses_one_arxiv_lookup(self):
        reader = FakeReader([page([cited('2001.00001')]), page([cited('2001.00001')])])
        dates = FakeDates({'2001.00001': DATE})
        results = ReferencesModule(reader, dates).fetch_references_batch(TASKS, CTX)
        self.assertEqual(dates.calls, [['2001.00001']])
        self.assertEqual([r['status'] for r in results], ['success', 'success'])
        self.assertEqual(sum(r['attempts'] for r in results), 3)
        for task, r in zip(TASKS, results):
            self.assertEqual(r['data']['references']['2001.00001']['submission_date'], DATE)
            self.assertEqual(r['missing_fields'], [])
            self.assertEqual(validate_for_task(r, paper_spec(task, 'references'), CTX), [])

    def test_different_ids_are_merged_into_one_arxiv_call(self):
        reader = FakeReader([page([cited('2001.00001')]), page([cited('2001.00002')])])
        dates = FakeDates({'2001.00001': DATE, '2001.00002': DATE})
        results = ReferencesModule(reader, dates).fetch_references_batch(TASKS, CTX)
        self.assertEqual(dates.calls, [['2001.00001', '2001.00002']])
        self.assertTrue(all(r['status'] == 'success' for r in results))

    def test_date_failure_keeps_raw_refs_for_retry(self):
        reader = FakeReader([page([cited('2001.00001')]), page([cited('2001.00002')])])
        dates = FakeDates(error=RemoteError('network_error', 'offline', True))
        results = ReferencesModule(reader, dates).fetch_references_batch(TASKS, CTX)
        for task, r in zip(TASKS, results):
            self.assertEqual(r['status'], 'partial')
            self.assertEqual(r['error']['code'], 'network_error')
            self.assertTrue(r['data']['raw_references'])
            self.assertEqual(validate_for_task(r, paper_spec(task, 'references'), CTX), [])

    def test_s2_429_stops_prefetching_more_papers(self):
        reader = FakeReader([RemoteError('rate_limited', 'HTTP 429', True, 429),
                             page([cited('2001.00001')])])
        dates = FakeDates({'2001.00001': DATE})
        results = ReferencesModule(reader, dates).fetch_references_batch(TASKS, CTX)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['status'], 'failed')
        self.assertEqual(results[0]['error']['http_status'], 429)
        self.assertEqual(len(reader.calls), 1)
        self.assertEqual(dates.calls, [])
