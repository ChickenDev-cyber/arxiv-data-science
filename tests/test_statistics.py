import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
import unittest

from arxiv_pipeline.collection.inspector import RunInspector
from arxiv_pipeline.config import ASSIGNED_RANGES, OWNERS
from arxiv_pipeline.contracts import SCHEMA_VERSION, Summary
from arxiv_pipeline.pipeline_contracts import (
    CollectionManifest,
    CollectionStatistics,
    FileDigest,
    RunReader,
    VerifiedCollection,
    VerifiedRun,
)
from arxiv_pipeline.statistics.service import (
    StatisticsService,
    compute_summary,
)
from arxiv_pipeline.statistics.cli import main as cli_main
from arxiv_pipeline.validation.pipeline_contracts import validate_pipeline_document


class MockRunReader:
    """Mock RunReader for testing verified run handling without runner locks."""

    def __init__(self, run: VerifiedRun):
        self.run = run

    @contextmanager
    def open(self, output_root: Path, run_id: str):
        yield self.run


class TestStatistics(unittest.TestCase):
    def setUp(self):
        self.test_root = Path("tests/fixtures/test_stats_run")
        self.run_id = "test-run-001"
        self.run_dir = self.test_root / "runs" / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)

        # 1. Create mock scope.json
        self.scope_data = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "is_mock": True,
            "scope": {
                "ranges": [
                    {
                        "owner": "hoang",
                        "start_month": "2025-03",
                        "start_id": 6914,
                        "end_month": "2025-03",
                        "end_id": 11913,
                        "inclusive": True,
                    },
                    {
                        "owner": "trong",
                        "start_month": "2025-04",
                        "start_id": 17523,
                        "end_month": "2025-05",
                        "end_id": 665,
                        "inclusive": True,
                    },
                ],
                "paper_tasks": [
                    {"owner": "hoang", "arxiv_id": "2503.06914"},
                    {"owner": "trong", "arxiv_id": "2504.17523"},
                ],
                "discovery_complete": True,
                "versions_complete": True,
                "owner_count": 2,
                "paper_count": 2,
            },
        }
        with open(self.run_dir / "scope.json", "w", encoding="utf-8") as f:
            json.dump(self.scope_data, f, indent=2)

        # 2. Create mock validation.json
        self.validation_data = {"valid": True, "issues": []}
        with open(self.run_dir / "validation.json", "w", encoding="utf-8") as f:
            json.dump(self.validation_data, f, indent=2)

        # 3. Create mock accepted_results.jsonl
        self.mock_results = [
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": self.run_id,
                "owner": "hoang",
                "is_mock": True,
                "step": "discovery",
                "arxiv_id": None,
                "version": None,
                "status": "success",
                "data": {
                    "papers": [{"owner": "hoang", "arxiv_id": "2503.06914"}],
                    "unresolved_ids": [],
                    "excluded_ids": [],
                    "complete": True,
                },
                "missing_fields": [],
                "error": None,
                "provenance": {},
                "metrics": {},
                "started_at": "2025-03-01T00:00:00Z",
                "finished_at": "2025-03-01T00:00:01Z",
                "duration_seconds": 1.0,
                "attempts": 1,
            },
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": self.run_id,
                "owner": "trong",
                "is_mock": True,
                "step": "discovery",
                "arxiv_id": None,
                "version": None,
                "status": "success",
                "data": {
                    "papers": [{"owner": "trong", "arxiv_id": "2504.17523"}],
                    "unresolved_ids": [],
                    "excluded_ids": [],
                    "complete": True,
                },
                "missing_fields": [],
                "error": None,
                "provenance": {},
                "metrics": {},
                "started_at": "2025-04-01T00:00:00Z",
                "finished_at": "2025-04-01T00:00:01Z",
                "duration_seconds": 1.0,
                "attempts": 1,
            },
            # hoang - metadata
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": self.run_id,
                "owner": "hoang",
                "is_mock": True,
                "step": "metadata",
                "arxiv_id": "2503.06914",
                "version": None,
                "status": "success",
                "data": {
                    "arxiv_id": "2503.06914",
                    "title": "A Great Paper",
                    "authors": ["Author One"],
                    "submission_date": "2025-03-01T00:00:00Z",
                    "revised_dates": ["2025-03-05T00:00:00Z"],
                    "venue": None,
                    "versions": [
                        {"version": 1, "submitted_at": "2025-03-01T00:00:00Z"},
                        {"version": 2, "submitted_at": "2025-03-05T00:00:00Z"},
                    ],
                    "versions_complete": True,
                },
                "missing_fields": [],
                "error": None,
                "provenance": {},
                "metrics": {},
                "started_at": "2025-03-01T00:00:00Z",
                "finished_at": "2025-03-01T00:00:01Z",
                "duration_seconds": 1.0,
                "attempts": 1,
            },
            # hoang - source v1
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": self.run_id,
                "owner": "hoang",
                "is_mock": True,
                "step": "sources",
                "arxiv_id": "2503.06914",
                "version": 1,
                "status": "success",
                "data": {
                    "source_kind": "tar_gzip",
                    "source_dir": "raw/hoang/2503-06914/v1",
                    "files": [{"path": "raw/hoang/2503-06914/v1/main.tex", "size_bytes": 1500}],
                },
                "missing_fields": [],
                "error": None,
                "provenance": {},
                "metrics": {
                    "download_bytes": 1000,
                    "extracted_bytes_before_filter": 2000,
                    "retained_source_bytes": 1500,
                    "removed_figure_bytes": 500,
                    "removed_other_bytes": 0,
                },
                "started_at": "2025-03-01T00:00:01Z",
                "finished_at": "2025-03-01T00:00:02Z",
                "duration_seconds": 1.0,
                "attempts": 1,
            },
            # hoang - source v2
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": self.run_id,
                "owner": "hoang",
                "is_mock": True,
                "step": "sources",
                "arxiv_id": "2503.06914",
                "version": 2,
                "status": "success",
                "data": {
                    "source_kind": "tar_gzip",
                    "source_dir": "raw/hoang/2503-06914/v2",
                    "files": [{"path": "raw/hoang/2503-06914/v2/main.tex", "size_bytes": 2500}],
                },
                "missing_fields": [],
                "error": None,
                "provenance": {},
                "metrics": {
                    "download_bytes": 1200,
                    "extracted_bytes_before_filter": 3000,
                    "retained_source_bytes": 2500,
                    "removed_figure_bytes": 500,
                    "removed_other_bytes": 0,
                },
                "started_at": "2025-03-01T00:00:02Z",
                "finished_at": "2025-03-01T00:00:03Z",
                "duration_seconds": 1.0,
                "attempts": 1,
            },
            # hoang - references
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": self.run_id,
                "owner": "hoang",
                "is_mock": True,
                "step": "references",
                "arxiv_id": "2503.06914",
                "version": None,
                "status": "success",
                "data": {
                    "complete": True,
                    "raw_references": [{"id": "1"}, {"id": "2"}],
                    "references": {
                        "2001.00001": {
                            "arxiv_id": "2001.00001",
                            "title": "Reference One",
                            "authors": ["Ref Author"],
                            "submission_date": "2020-01-01",
                            "semantic_scholar_id": "s2_001",
                        },
                        "2001.00002": {
                            "arxiv_id": "2001.00002",
                            "title": "Reference Two",
                            "authors": ["Ref Author 2"],
                            "submission_date": "2020-01-02",
                            "semantic_scholar_id": "s2_002",
                        },
                    },
                },
                "missing_fields": [],
                "error": None,
                "provenance": {},
                "metrics": {
                    "s2_reference_count": 2,
                    "arxiv_reference_count": 2,
                    "complete_reference_count": 2,
                },
                "started_at": "2025-03-01T00:00:03Z",
                "finished_at": "2025-03-01T00:00:04Z",
                "duration_seconds": 1.0,
                "attempts": 1,
            },
            # trong - metadata
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": self.run_id,
                "owner": "trong",
                "is_mock": True,
                "step": "metadata",
                "arxiv_id": "2504.17523",
                "version": None,
                "status": "success",
                "data": {
                    "arxiv_id": "2504.17523",
                    "title": "Another Paper",
                    "authors": ["Author Two"],
                    "submission_date": "2025-04-01T00:00:00Z",
                    "revised_dates": [],
                    "venue": None,
                    "versions": [
                        {"version": 1, "submitted_at": "2025-04-01T00:00:00Z"},
                    ],
                    "versions_complete": True,
                },
                "missing_fields": [],
                "error": None,
                "provenance": {},
                "metrics": {},
                "started_at": "2025-04-01T00:00:00Z",
                "finished_at": "2025-04-01T00:00:01Z",
                "duration_seconds": 1.0,
                "attempts": 1,
            },
            # trong - source v1 unsupported (e.g. PDF only)
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": self.run_id,
                "owner": "trong",
                "is_mock": True,
                "step": "sources",
                "arxiv_id": "2504.17523",
                "version": 1,
                "status": "unsupported",
                "data": {"source_kind": "pdf_only", "source_dir": "raw/trong/2504-17523/v1", "files": []},
                "missing_fields": [],
                "error": None,
                "provenance": {},
                "metrics": {
                    "download_bytes": 500,
                    "extracted_bytes_before_filter": 0,
                    "retained_source_bytes": 0,
                    "removed_figure_bytes": 0,
                    "removed_other_bytes": 0,
                },
                "started_at": "2025-04-01T00:00:01Z",
                "finished_at": "2025-04-01T00:00:02Z",
                "duration_seconds": 1.0,
                "attempts": 1,
            },
            # trong - references empty
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": self.run_id,
                "owner": "trong",
                "is_mock": True,
                "step": "references",
                "arxiv_id": "2504.17523",
                "version": None,
                "status": "empty",
                "data": {
                    "complete": True,
                    "raw_references": [],
                    "references": {},
                },
                "missing_fields": [],
                "error": None,
                "provenance": {},
                "metrics": {
                    "s2_reference_count": 0,
                    "arxiv_reference_count": 0,
                    "complete_reference_count": 0,
                },
                "started_at": "2025-04-01T00:00:02Z",
                "finished_at": "2025-04-01T00:00:03Z",
                "duration_seconds": 1.0,
                "attempts": 1,
            },
        ]
        with open(self.run_dir / "accepted_results.jsonl", "w", encoding="utf-8") as f:
            for item in self.mock_results:
                f.write(json.dumps(item) + "\n")

    def tearDown(self):
        import shutil
        if self.test_root.exists():
            shutil.rmtree(self.test_root, ignore_errors=True)

    def test_default_run_reader_is_run_inspector(self):
        """Default reader in StatisticsService must be RunInspector."""
        service = StatisticsService()
        self.assertIsInstance(service.run_reader, RunInspector)

    def test_build_statistics_single_run(self):
        service = StatisticsService(check_validation=False)
        stats = service.build_statistics(self.test_root, self.run_id)

        self.assertEqual(stats["schema_version"], SCHEMA_VERSION)
        self.assertEqual(stats["run_id"], self.run_id)
        self.assertTrue(stats["is_mock"])

        summary = stats["summary"]
        self.assertEqual(summary["papers_discovered"], 2)
        self.assertEqual(summary["metadata_complete_papers"], 2)
        self.assertEqual(summary["papers_complete"], 1)
        self.assertEqual(summary["paper_success_rate"], 0.5)

        # Sources
        self.assertEqual(summary["source_versions_expected"], 3)  # hoang 2 + trong 1
        self.assertEqual(summary["source_versions_success"], 2)
        self.assertEqual(summary["source_versions_unsupported"], 1)
        self.assertEqual(summary["source_versions_unresolved"], 0)
        self.assertAlmostEqual(summary["source_success_rate"], 2 / 3)

        # References
        self.assertEqual(summary["references_fully_fetched_papers"], 2)
        self.assertEqual(summary["references_arxiv_total"], 2)
        self.assertEqual(summary["references_complete_total"], 2)
        self.assertEqual(summary["reference_metadata_success_rate"], 1.0)
        self.assertEqual(summary["mean_arxiv_references_per_paper"], 1.0)

        # Check by_owner breakdown
        by_owner = stats["by_owner"]
        self.assertIn("hoang", by_owner)
        self.assertIn("trong", by_owner)
        self.assertEqual(by_owner["hoang"]["papers_complete"], 1)
        self.assertEqual(by_owner["trong"]["papers_complete"], 0)

    def test_summary_schema_no_timing(self):
        """Verify Summary strictly adheres to the 20 fields without injecting uncontracted timing."""
        service = StatisticsService(check_validation=False)
        stats = service.build_statistics(self.test_root, self.run_id)
        summary = stats["summary"]

        self.assertNotIn("discovery_seconds", summary)
        self.assertNotIn("mean_paper_seconds", summary)
        self.assertNotIn("per_paper_seconds", summary)
        self.assertEqual(len(summary.keys()), 20)

    def test_corpus_missing_file_rejected(self):
        """Corpus verification fails when an artifact file recorded in run is missing on disk."""
        missing_file_digest: FileDigest = {
            "path": "raw/hoang/missing.tex",
            "size_bytes": 100,
            "sha256": "0" * 64,
        }
        v_run = VerifiedRun(
            output_root=self.run_dir,
            scope=self.scope_data,
            accepted_results=self.mock_results,
            validation={"valid": True, "issues": []},
            quality={"valid": True, "issues": []},
            files=[missing_file_digest],
            run_state="finished",
            config_fingerprint="f" * 64,
            recorded_output_root=str(self.test_root),
        )
        service = StatisticsService(run_reader=MockRunReader(v_run))
        with self.assertRaises(ValueError) as ctx:
            service.build_statistics(self.test_root, self.run_id)
        self.assertIn("Corpus file missing", str(ctx.exception))

    def test_corpus_hash_mismatch_rejected(self):
        """Corpus verification fails when an artifact file has a modified hash."""
        test_file = self.run_dir / "test_artifact.tex"
        test_file.write_bytes(b"actual content")
        bad_digest: FileDigest = {
            "path": "runs/test-run-001/test_artifact.tex",
            "size_bytes": len(b"actual content"),
            "sha256": "a" * 64,  # wrong hash
        }
        v_run = VerifiedRun(
            output_root=self.test_root,
            scope=self.scope_data,
            accepted_results=self.mock_results,
            validation={"valid": True, "issues": []},
            quality={"valid": True, "issues": []},
            files=[bad_digest],
            run_state="finished",
            config_fingerprint="f" * 64,
            recorded_output_root=str(self.test_root),
        )
        service = StatisticsService(run_reader=MockRunReader(v_run))
        with self.assertRaises(ValueError) as ctx:
            service.build_statistics(self.test_root, self.run_id)
        self.assertIn("Corpus file hash mismatch", str(ctx.exception))

    def test_summarize_run_owner(self):
        """StatisticsService implements OwnerSummarizer.summarize(run, owner)."""
        v_run = VerifiedRun(
            output_root=self.test_root,
            scope=self.scope_data,
            accepted_results=self.mock_results,
            validation={"valid": True, "issues": []},
            quality={"valid": True, "issues": []},
            files=[],
            run_state="finished",
            config_fingerprint="f" * 64,
            recorded_output_root=str(self.test_root),
        )
        service = StatisticsService(check_validation=False)
        hoang_summary = service.summarize(v_run, "hoang")
        self.assertEqual(hoang_summary["papers_discovered"], 1)
        self.assertEqual(hoang_summary["papers_complete"], 1)
        self.assertEqual(hoang_summary["source_versions_expected"], 2)

    def test_scope_empty_paper_tasks_gives_empty_by_owner(self):
        """When scope has no paper tasks, by_owner is strictly empty ({})."""
        empty_scope_data = {
            "schema_version": SCHEMA_VERSION,
            "run_id": "empty-run",
            "is_mock": True,
            "scope": {
                "ranges": [],
                "paper_tasks": [],
                "discovery_complete": False,
                "versions_complete": False,
                "owner_count": 0,
                "paper_count": 0,
            },
        }
        empty_dir = self.test_root / "runs" / "empty-run"
        empty_dir.mkdir(parents=True, exist_ok=True)
        (empty_dir / "scope.json").write_text(json.dumps(empty_scope_data), encoding="utf-8")
        (empty_dir / "accepted_results.jsonl").write_text("", encoding="utf-8")

        service = StatisticsService(check_validation=False)
        stats = service.build_statistics(self.test_root, "empty-run")
        self.assertEqual(stats["by_owner"], {})
        self.assertEqual(stats["summary"]["papers_discovered"], 0)

    def test_discovery_complete_requires_all_five_ranges(self):
        """discovery_complete in aggregated scope is true ONLY when all 5 assigned ranges are present and complete."""
        service = StatisticsService(check_validation=False)
        # 1. Only 2 ranges -> discovery_complete MUST be False
        stats1 = service.build_statistics(self.test_root, self.run_id)
        col_partial = service.aggregate_statistics([stats1], collection_id="partial-col")
        self.assertFalse(col_partial["scope"]["discovery_complete"])

        # 2. Build 5 distinct ranges for all 5 canonical owners
        canonical_ids = {
            "khang": "2506.20788",
            "trong": "2504.17523",
            "hoang": "2503.06914",
            "man-nhi": "2501.12642",
            "huynh-nhi": "2409.03594",
        }
        stats_all = []
        for owner in OWNERS:
            st = service.build_statistics(self.test_root, self.run_id)
            aid = canonical_ids[owner]
            st["scope"] = {
                "ranges": [dict(c) for c in ASSIGNED_RANGES if c["owner"] == owner],
                "paper_tasks": [{"owner": owner, "arxiv_id": aid}],
                "discovery_complete": True,
                "versions_complete": True,
                "owner_count": 1,
                "paper_count": 1,
            }
            st["by_owner"] = {owner: compute_summary(st["scope"]["paper_tasks"], [])}
            stats_all.append(st)

        col_full = service.aggregate_statistics(stats_all, collection_id="full-col")
        self.assertTrue(col_full["scope"]["discovery_complete"])
        self.assertEqual(len(col_full["scope"]["ranges"]), 5)
        self.assertEqual(col_full["missing_owners"], [])

    def test_aggregate_statistics_schema_conformance_no_run_id(self):
        """CollectionStatistics must NOT contain run_id and must pass validate_pipeline_document."""
        service = StatisticsService(check_validation=False)
        stats = service.build_statistics(self.test_root, self.run_id)
        col_stats = service.aggregate_statistics([stats], collection_id="valid-col")

        self.assertNotIn("run_id", col_stats)
        self.assertEqual(col_stats["format_version"], 1)
        self.assertEqual(col_stats["kind"], "arxiv-collection-statistics")
        self.assertEqual(col_stats["schema_version"], SCHEMA_VERSION)
        problems = validate_pipeline_document(col_stats, "collection-statistics")
        self.assertEqual(problems, [])

    def test_aggregate_statistics_with_verified_collection(self):
        """aggregate_statistics integrates directly with VerifiedCollection via CollectionStatisticsBuilder."""
        service = StatisticsService(check_validation=False)
        manifest: CollectionManifest = {
            "format_version": 1,
            "kind": "arxiv-collection",
            "collection_id": "test-col",
            "schema_version": SCHEMA_VERSION,
            "is_mock": True,
            "created_at": "2026-10-31T00:00:00Z",
            "members": [
                {
                    "snapshot_id": "snap-hoang",
                    "run_id": "run-hoang",
                    "owners": ["hoang"],
                    "exporter_commit": "a" * 40,
                    "root": "snap-hoang/corpus",
                    "manifest_path": "snap-hoang/manifest.json",
                }
            ],
        }
        v_run = VerifiedRun(
            output_root=self.test_root,
            scope={
                "schema_version": SCHEMA_VERSION,
                "run_id": "run-hoang",
                "is_mock": True,
                "scope": {
                    "ranges": [dict(c) for c in ASSIGNED_RANGES if c["owner"] == "hoang"],
                    "paper_tasks": [{"owner": "hoang", "arxiv_id": "2503.06914"}],
                    "discovery_complete": True,
                    "versions_complete": False,
                    "owner_count": 1,
                    "paper_count": 1,
                },
            },
            accepted_results=[r for r in self.mock_results if r["owner"] == "hoang"],
            validation={"valid": True, "issues": []},
            quality={"valid": True, "issues": []},
            files=[],
            run_state="finished",
            config_fingerprint="f" * 64,
            recorded_output_root=str(self.test_root),
        )
        v_col = VerifiedCollection(manifest=manifest, runs=[v_run])
        col_stats = service.aggregate_statistics(v_col)
        self.assertEqual(col_stats["collection_id"], "test-col")
        self.assertIn("hoang", col_stats["by_owner"])
        self.assertEqual(col_stats["summary"]["papers_discovered"], 1)
        self.assertEqual(validate_pipeline_document(col_stats, "collection-statistics"), [])

    def test_reference_incomplete_page_excluded(self):
        """Exclude references with complete != True from references_arxiv_total and complete totals."""
        task: list = [{"owner": "hoang", "arxiv_id": "2501.00001"}]
        results = [
            {
                "owner": "hoang",
                "step": "references",
                "arxiv_id": "2501.00001",
                "status": "partial",
                "data": {
                    "complete": False,  # incomplete pagination
                    "raw_references": [{"id": "1"}],
                    "references": {"ref1": {}},
                },
                "metrics": {
                    "s2_reference_count": 10,
                    "arxiv_reference_count": 5,
                    "complete_reference_count": 5,
                },
            }
        ]
        summary = compute_summary(task, results)  # type: ignore
        self.assertEqual(summary["references_fully_fetched_papers"], 0)
        self.assertEqual(summary["references_arxiv_total"], 0)
        self.assertEqual(summary["references_complete_total"], 0)
        self.assertIsNone(summary["mean_arxiv_references_per_paper"])

    def test_source_bytes_complete_pair_and_success_only(self):
        """Only status == 'success' sources with both extracted and retained non-negative counts are sampled."""
        task: list = [{"owner": "hoang", "arxiv_id": "2501.00001"}]
        results = [
            # 1. success with both numbers -> included
            {
                "owner": "hoang",
                "step": "sources",
                "arxiv_id": "2501.00001",
                "version": 1,
                "status": "success",
                "metrics": {
                    "extracted_bytes_before_filter": 1000,
                    "retained_source_bytes": 400,
                },
            },
            # 2. unsupported with numbers -> excluded
            {
                "owner": "hoang",
                "step": "sources",
                "arxiv_id": "2501.00001",
                "version": 2,
                "status": "unsupported",
                "metrics": {
                    "extracted_bytes_before_filter": 500,
                    "retained_source_bytes": 0,
                },
            },
            # 3. success with missing pair (only extracted present) -> excluded
            {
                "owner": "hoang",
                "step": "sources",
                "arxiv_id": "2501.00001",
                "version": 3,
                "status": "success",
                "metrics": {
                    "extracted_bytes_before_filter": 800,
                    "retained_source_bytes": None,
                },
            },
        ]
        summary = compute_summary(task, results)  # type: ignore
        self.assertEqual(summary["source_size_sample_count"], 1)
        self.assertEqual(summary["mean_extracted_bytes_before_filter"], 1000.0)
        self.assertEqual(summary["mean_retained_source_bytes"], 400.0)

    def test_aggregate_statistics_reject_mixed_mock(self):
        """Reject aggregation when mock runs and real runs are mixed."""
        service = StatisticsService(check_validation=False)
        stats_mock = service.build_statistics(self.test_root, self.run_id)
        stats_mock["is_mock"] = True

        stats_real = service.build_statistics(self.test_root, self.run_id)
        stats_real["is_mock"] = False
        stats_real["run_id"] = "test-real"

        with self.assertRaises(ValueError) as ctx:
            service.aggregate_statistics([stats_mock, stats_real])
        self.assertIn("mixed mock and real", str(ctx.exception))

    def test_aggregate_statistics_reject_duplicate_owner(self):
        """Reject aggregation when the same owner appears across multiple aggregated runs."""
        service = StatisticsService(check_validation=False)
        stats1 = service.build_statistics(self.test_root, self.run_id)
        stats2 = service.build_statistics(self.test_root, self.run_id)
        stats2["run_id"] = "test-run-002"

        with self.assertRaises(ValueError) as ctx:
            service.aggregate_statistics([stats1, stats2])
        self.assertIn("Duplicate owner", str(ctx.exception))

    def test_cli_execution(self):
        output_dir = self.test_root / "output_stats"
        exit_code = cli_main([
            "--run-id", self.run_id,
            "--output-root", str(self.test_root),
            "--output-dir", str(output_dir),
            "--no-validation",
        ])
        self.assertEqual(exit_code, 0)
        stat_file = output_dir / f"{self.run_id}.json"
        self.assertTrue(stat_file.exists())


if __name__ == "__main__":
    unittest.main()
