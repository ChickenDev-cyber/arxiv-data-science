from dataclasses import FrozenInstanceError
from importlib import import_module, util
from pathlib import Path
import unittest

from arxiv_pipeline.validation.results import _check


class PipelineContractTests(unittest.TestCase):
    def contracts(self):
        self.assertIsNotNone(util.find_spec("arxiv_pipeline.pipeline_contracts"),
                             "The shared pipeline contracts are not implemented")
        return import_module("arxiv_pipeline.pipeline_contracts")

    def test_contract_names_and_fields_match_spec(self):
        contracts = self.contracts()
        manifest = {
            "format_version": 1, "kind": "arxiv-snapshot", "snapshot_id": "sample",
            "schema_version": "1.2", "run_id": "sample", "is_mock": True,
            "owners": ["trong"], "created_at": "2026-10-27T00:00:00Z",
            "exporter_commit": "a" * 40, "producer_commits": [],
            "unknown_provenance_tasks": 1, "config_fingerprint": "b" * 64,
            "origin_output_root": "/tmp/sample",
            "files": [{"path": "raw/trong/2504-17523/metadata.json",
                       "size_bytes": 20, "sha256": "c" * 64}],
        }
        self.assertEqual(_check(manifest, contracts.SnapshotManifest, ""), [])
        self.assertTrue(_check({**manifest, "format_version": True}, contracts.SnapshotManifest, ""))
        self.assertTrue(_check({**manifest, "owners": ["unknown"]}, contracts.SnapshotManifest, ""))
        self.assertTrue(_check({**manifest, "code_commit": "d" * 40}, contracts.SnapshotManifest, ""))

    def test_verified_run_is_frozen(self):
        contracts = self.contracts()
        scope = {"schema_version": "1.2", "run_id": "sample", "is_mock": True,
                 "scope": {"ranges": [], "paper_tasks": [], "discovery_complete": False,
                           "versions_complete": False, "owner_count": 0, "paper_count": 0}}
        verified = contracts.VerifiedRun(
            output_root=Path("/tmp/sample"), scope=scope, accepted_results=[],
            validation={"valid": True, "issues": []}, quality={"valid": False, "issues": []},
            files=[], run_state="paused", config_fingerprint="b" * 64,
            recorded_output_root="/original/sample")
        self.assertEqual(verified.scope["run_id"], "sample")
        self.assertEqual(verified.recorded_output_root, "/original/sample")
        with self.assertRaises(FrozenInstanceError):
            verified.output_root = Path("/other")

    def test_checkpoint_payloads_have_interoperable_types(self):
        contracts = self.contracts()
        record = {"format_version": 1, "namespace": "arxiv_dates", "key": '["2504.17523"]',
                  "payload": {"arxiv_id": "2504.17523", "submission_date": "2025-04-24",
                              "source": "https://export.arxiv.org/api/query",
                              "fetched_at": "2026-10-27T00:00:00Z"},
                  "updated_at": "2026-10-27T00:00:00Z"}
        self.assertEqual(_check(record, contracts.CheckpointRecord, ""), [])
        self.assertTrue(_check({**record, "namespace": "unknown"}, contracts.CheckpointRecord, ""))
        self.assertTrue(_check({**record, "payload": {}}, contracts.CheckpointRecord, ""))
