"""Progress counts describe processed candidates, independently of success."""
from inspect import signature
from pathlib import Path
import unittest

from arxiv_pipeline.discovery import DiscoveryModule
from arxiv_pipeline.discovery.api import RemoteError
from arxiv_pipeline.runner.checkpoints import InMemoryCheckpointStore
from test_discovery import FakeClient, config, context, feed


class DiscoveryProgressTests(unittest.TestCase):
    def discover(self, module, cfg, events):
        self.assertIn('progress', signature(module.discover).parameters,
                      'Discovery does not report batch progress yet')
        return module.discover(cfg, context(Path('/tmp')), progress=lambda n, total: events.append((n, total)))

    def test_reports_completed_batches_with_a_short_last_batch(self):
        client = FakeClient([feed(), feed(), feed()])
        events = []
        result = self.discover(DiscoveryModule(client, batch_size=2), config(17523, 17527), events)
        self.assertEqual(events, [(0, 5), (2, 5), (4, 5), (5, 5)])
        self.assertEqual([len(ids) for ids in client.calls], [2, 2, 1])
        self.assertEqual(result['status'], 'success')

    def test_sample_progress_uses_the_candidate_limit(self):
        events = []
        result = self.discover(DiscoveryModule(FakeClient([feed()]), max_candidates=2),
                               config(17523, 17527), events)
        self.assertEqual(events, [(0, 2), (2, 2)])
        self.assertFalse(result['data']['complete'])
        self.assertEqual(result['status'], 'partial')

    def test_cached_batches_advance_progress_without_network_requests(self):
        store = InMemoryCheckpointStore()
        cfg = config(17523, 17527)
        DiscoveryModule(FakeClient([feed(), feed(), feed()]), batch_size=2,
                        checkpoints=store).discover(cfg, context(Path('/tmp')))
        client = FakeClient([])
        events = []
        result = self.discover(DiscoveryModule(client, batch_size=2, checkpoints=store), cfg, events)
        self.assertEqual(events, [(0, 5), (2, 5), (4, 5), (5, 5)])
        self.assertEqual(client.calls, [])
        self.assertEqual(result['attempts'], 0)

    def test_failed_batch_counts_as_processed_without_becoming_success(self):
        events = []
        result = self.discover(DiscoveryModule(FakeClient([RemoteError('network_error', 'offline', True)])),
                               config(17523, 17524), events)
        self.assertEqual(events, [(0, 2), (2, 2)])
        self.assertEqual(result['status'], 'failed')
        self.assertIsNotNone(result['error'])

    def test_interruption_does_not_count_an_unfinished_batch(self):
        client = FakeClient([feed()])
        original = client.query

        def interrupt(ids):
            if client.calls:
                raise KeyboardInterrupt()
            return original(ids)

        client.query = interrupt
        events = []
        with self.assertRaises(KeyboardInterrupt):
            self.discover(DiscoveryModule(client, batch_size=2), config(17523, 17527), events)
        self.assertEqual(events, [(0, 5), (2, 5)])
