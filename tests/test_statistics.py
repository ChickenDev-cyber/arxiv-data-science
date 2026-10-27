import json
from pathlib import Path
import unittest

from arxiv_pipeline.contracts import SCHEMA_VERSION
from arxiv_pipeline.statistics.service import StatisticsService, compute_summary
from arxiv_pipeline.statistics.cli import main as cli_main


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

        # 2. Create mock accepted_results.jsonl
        # hoang: 2503.06914 has metadata, 2 versions sources, references
        # trong: 2504.17523 has metadata, 1 version unsupported source, references
        self.mock_results = [
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
                "data": {"source_kind": "tar_gzip", "source_dir": "raw/hoang/2503-06914/v1", "files": []},
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
                "data": {"source_kind": "tar_gzip", "source_dir": "raw/hoang/2503-06914/v2", "files": []},
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
                    "references": {"2001.00001": {}, "2001.00002": {}},
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

    def test_build_statistics_single_run(self):
        service = StatisticsService(check_validation=False)
        stats = service.build_statistics(self.test_root, self.run_id)

        self.assertEqual(stats["schema_version"], SCHEMA_VERSION)
        self.assertEqual(stats["run_id"], self.run_id)
        self.assertTrue(stats["is_mock"])

        summary = stats["summary"]
        self.assertEqual(summary["papers_discovered"], 2)
        self.assertEqual(summary["metadata_complete_papers"], 2)
        # hoang paper is complete (metadata + 2 sources success + ref success)
        # trong paper has unsupported source -> not complete
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

    def test_aggregate_statistics_multi_runs(self):
        service = StatisticsService(check_validation=False)
        stats1 = service.build_statistics(self.test_root, self.run_id)

        # Create a second stats object for aggregation test
        stats2 = service.build_statistics(self.test_root, self.run_id)
        stats2["run_id"] = "test-run-002"

        aggregated = service.aggregate_statistics([stats1, stats2], aggregated_run_id="multi-run-test")
        self.assertEqual(aggregated["run_id"], "multi-run-test")
        self.assertEqual(aggregated["schema_version"], SCHEMA_VERSION)
        self.assertEqual(aggregated["summary"]["papers_discovered"], 2)

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
