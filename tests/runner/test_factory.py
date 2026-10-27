from importlib import import_module, util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from arxiv_pipeline.http import RequestScheduler
from arxiv_pipeline.references.service import ReferencesModule
from arxiv_pipeline.runner.checkpoints import InMemoryCheckpointStore
from arxiv_pipeline.runner.models import RunOptions


class FactoryTests(unittest.TestCase):
    def factory_module(self):
        module = import_module("arxiv_pipeline.runner.factory")
        self.assertTrue(hasattr(module, "LiveModuleFactory"), "Live module composition is missing")
        return module

    def test_factory_injects_the_same_store(self):
        module = self.factory_module()

        class CheckpointScheduler(RequestScheduler):
            def __init__(self, *, checkpoints):
                super().__init__()
                self.checkpoints = checkpoints

        class CheckpointReferences(ReferencesModule):
            def __init__(self, reader, dates, *, checkpoints):
                super().__init__(reader, dates)
                self.checkpoints = checkpoints

        options = RunOptions("factory", Path("/tmp/factory"), ("trong",))
        store = InMemoryCheckpointStore()
        with patch("arxiv_pipeline.http.RequestScheduler", CheckpointScheduler), \
             patch("arxiv_pipeline.references.service.ReferencesModule", CheckpointReferences):
            built = module.LiveModuleFactory().build(options, store)
        self.assertIs(built.discovery.checkpoints, store)
        self.assertIs(built.references.checkpoints, store)
        scheduler = built.discovery.client.scheduler
        self.assertIs(scheduler.checkpoints, store)
        self.assertIs(built.metadata.client.scheduler, scheduler)
        self.assertIs(built.sources.client.scheduler, scheduler)
        self.assertIs(built.references.reader.scheduler, scheduler)
        self.assertIs(built.references.dates.scheduler, scheduler)

    def test_baseline_clients_remain_constructible_without_checkpoint_keyword(self):
        module = self.factory_module()
        options = RunOptions("baseline-build", Path("/tmp/baseline-build"), ("trong",))
        built = module.LiveModuleFactory().build(options, InMemoryCheckpointStore())
        self.assertIsInstance(built.references, ReferencesModule)
        self.assertIsInstance(built.discovery.client.scheduler, RequestScheduler)

    def test_mock_factory_writes_discovery_to_supplied_store(self):
        module = self.factory_module()
        options = RunOptions("mock-build", Path("/tmp/mock-build"), ("trong",), True)
        store = InMemoryCheckpointStore()
        built = module.MockModuleFactory().build(options, store)
        result = built.discovery.discover(options.ranges[0], options.context)
        self.assertEqual(result["attempts"], 0)
        key = '["trong",{"end_id":665,"end_month":"2025-05","inclusive":true,"owner":"trong","start_id":17523,"start_month":"2025-04"},10,100,0]'
        record = store.load("discovery_batches", key)
        self.assertIsNotNone(record)
        self.assertEqual(record["payload"]["found_ids"], ["2504.17523"])

    def test_broken_future_constructor_does_not_silently_fall_back(self):
        module = self.factory_module()

        class BrokenReferences:
            def __init__(self, reader, dates, *, checkpoints):
                raise TypeError("member constructor bug")

        with patch("arxiv_pipeline.references.service.ReferencesModule", BrokenReferences):
            with self.assertRaisesRegex(TypeError, "member constructor bug"):
                module.LiveModuleFactory().build(
                    RunOptions("broken", Path("/tmp/broken"), ("trong",)), InMemoryCheckpointStore())
