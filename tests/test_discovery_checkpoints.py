from copy import deepcopy
from pathlib import Path
import unittest

from arxiv_pipeline.discovery import DiscoveryModule
from arxiv_pipeline.discovery.api import RemoteError
from arxiv_pipeline.runner.checkpoints import InMemoryCheckpointStore
from test_discovery import config, context, feed, FakeClient


class DiscoveryCheckpointTests(unittest.TestCase):
    def test_interrupt_during_singletons_preserves_every_verified_response(self):
        store = InMemoryCheckpointStore()
        cfg = config(17523, 17525)
        first = FakeClient([feed(['2504.17523'], total=3), feed(['2504.17524'])])
        original = first.query

        def interrupt(ids):
            if len(first.calls) == 2:
                raise KeyboardInterrupt()
            return original(ids)

        first.query = interrupt
        with self.assertRaises(KeyboardInterrupt):
            self.module(first, store).discover(cfg, context(Path('/tmp')))
        second = FakeClient([feed(['2504.17525'])])
        result = self.module(second, store).discover(cfg, context(Path('/tmp')))
        self.assertEqual(second.calls, [['2504.17525']])
        self.assertEqual(len(result['data']['papers']), 3)
        self.assertEqual(result['attempts'], 1)
        self.assertIsNone(result['error'])

    def module(self, client, store, **kwargs):
        try:
            return DiscoveryModule(client, checkpoints=store, **kwargs)
        except TypeError as exc:
            self.fail(f"Discovery checkpoint injection is not implemented: {exc}")

    def test_resume_skips_completed_batch(self):
        store = InMemoryCheckpointStore()
        first = FakeClient([feed(["2504.17523", "2504.17524"]), KeyboardInterrupt()])
        # FakeClient only raises Exception subclasses; make interruption explicit.
        query = first.query

        def interrupt(ids):
            if len(first.calls) == 1:
                raise KeyboardInterrupt()
            return query(ids)

        first.query = interrupt
        with self.assertRaises(KeyboardInterrupt):
            self.module(first, store, batch_size=2).discover(config(17523, 17526), context(Path("/tmp")))
        second = FakeClient([feed(["2504.17525", "2504.17526"])])
        result = self.module(second, store, batch_size=2).discover(
            config(17523, 17526), context(Path("/tmp")))
        self.assertEqual(second.calls, [["2504.17525", "2504.17526"]])
        self.assertEqual(len(result["data"]["papers"]), 4)
        self.assertEqual(result["status"], "success")

    def test_resume_queries_only_unresolved_ids(self):
        store = InMemoryCheckpointStore()
        ids = [f"2504.{number:05d}" for number in range(17523, 17623)]
        replies = [feed(ids[:80], total=100)] + [feed() for _ in range(10)] + [
            RemoteError("network_error", "offline", True) for _ in range(10)]
        first = self.module(FakeClient(replies), store).discover(
            config(17523, 17622), context(Path("/tmp")))
        self.assertEqual(first["status"], "partial")
        self.assertEqual(first["data"]["unresolved_ids"], ids[90:])
        second = FakeClient([feed(ids[90:])])
        result = self.module(second, store).discover(config(17523, 17622), context(Path("/tmp")))
        self.assertEqual(second.calls, [ids[90:]])
        self.assertEqual(len(result["data"]["papers"]), 90)
        self.assertEqual(len(result["data"]["excluded_ids"]), 10)
        self.assertIsNone(result["error"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["attempts"], 1)

    def test_reused_batches_have_zero_attempts(self):
        store = InMemoryCheckpointStore()
        cfg = config(17523, 17523)
        self.module(FakeClient([feed(["2504.17523"])]), store).discover(cfg, context(Path("/tmp")))
        client = FakeClient([])
        result = self.module(client, store).discover(cfg, context(Path("/tmp")))
        self.assertEqual(client.calls, [])
        self.assertEqual(result["attempts"], 0)
        self.assertTrue(result["data"]["complete"])

    def test_corrupt_or_wrong_scope_checkpoint_is_rejected(self):
        for damage in ("owner", "partition", "key", "range"):
            with self.subTest(damage=damage):
                backing = InMemoryCheckpointStore()
                cfg = config(17523, 17523)
                self.module(FakeClient([feed(["2504.17523"])]), backing).discover(cfg, context(Path("/tmp")))

                class CorruptReader:
                    def load(self, namespace, key):
                        record = backing.load(namespace, key)
                        if damage == "owner":
                            record["payload"]["owner"] = "khang"
                        elif damage == "partition":
                            record["payload"]["excluded_ids"] = ["2504.17523"]
                        elif damage == "key":
                            record["key"] = '["wrong"]'
                        else:
                            record["payload"]["range_config"]["end_id"] = 17524
                        return record

                    def save(self, record):
                        raise AssertionError("Invalid checkpoint must not be rewritten")

                client = FakeClient([])
                with self.assertRaises(ValueError):
                    self.module(client, CorruptReader()).discover(cfg, context(Path("/tmp")))
                self.assertEqual(client.calls, [])

    def test_changed_sample_limit_uses_a_different_cache_key(self):
        store = InMemoryCheckpointStore()
        cfg = config(17523, 17525)
        partial = self.module(FakeClient([feed(["2504.17523"])]), store, max_candidates=1).discover(
            cfg, context(Path("/tmp")))
        self.assertFalse(partial["data"]["complete"])
        client = FakeClient([feed(["2504.17523", "2504.17524", "2504.17525"])])
        complete = self.module(client, store).discover(cfg, context(Path("/tmp")))
        self.assertEqual(len(client.calls[0]), 3)
        self.assertTrue(complete["data"]["complete"])

    def test_cross_month_does_not_stop_at_an_absent_id(self):
        cfg = config(99998, 2, end_month="2025-05")
        client = FakeClient([feed(), feed(["2505.00001", "2505.00002"])])
        result = self.module(client, InMemoryCheckpointStore(), batch_size=2).discover(
            cfg, context(Path("/tmp")))
        self.assertEqual(client.calls, [["2504.99998", "2504.99999"], ["2505.00001", "2505.00002"]])
        self.assertTrue(result["data"]["complete"])
        self.assertEqual(len(result["data"]["excluded_ids"]), 2)
