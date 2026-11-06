"""Offline unit tests for local_batch_runner."""
import json
from pathlib import Path
import tempfile
import unittest

from arxiv_pipeline.discovery.api import ApiResponse
from arxiv_pipeline.references.batch_client import BatchSemanticScholarClient
from arxiv_pipeline.references.local_batch_runner import process_batch_references
from arxiv_pipeline.runner.state import StateStore
from arxiv_pipeline.contracts import SCHEMA_VERSION


class LocalBatchRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "corpus"
        self.root.mkdir(parents=True)
        self.db_path = self.root / "state.sqlite"

        # Initialize mock state store
        from arxiv_pipeline.runner.models import RunOptions
        opts = RunOptions("test-run", str(self.root), ["trong"], is_mock=True, max_candidates=2, max_papers=2)
        with StateStore(self.db_path, "test-run") as store:
            store.open_run(opts)
            scope = {
                "ranges": [],
                "paper_tasks": [
                    {"owner": "trong", "arxiv_id": "2504.17523"},
                    {"owner": "trong", "arxiv_id": "2504.17524"}
                ],
                "discovery_complete": True,
                "versions_complete": True,
                "owner_count": 1,
                "paper_count": 2
            }
            store.update_scope(scope)

        # Create paper directories
        for aid in ("2504-17523", "2504-17524"):
            pdir = self.root / "raw" / "trong" / aid
            pdir.mkdir(parents=True)
            (pdir / "metadata.json").write_text(json.dumps({
                "arxiv_id": aid.replace("-", "."),
                "title": f"Paper {aid}",
                "authors": ["Author"],
                "submission_date": "2025-04-01T00:00:00Z",
                "revised_dates": [],
                "venue": None,
                "versions": [{"version": 1, "submitted_at": "2025-04-01T00:00:00Z"}],
                "versions_complete": True
            }))

    def test_process_batch_references(self):
        def fake_transport(url, timeout, headers, body=None):
            payload = [
                {
                    "paperId": "p1",
                    "title": "Paper 1",
                    "externalIds": {"ArXiv": "2504.17523"},
                    "referenceCount": 1,
                    "references": [
                        {
                            "paperId": "ref1",
                            "title": "Ref 1",
                            "authors": [{"name": "Ref Author"}],
                            "externalIds": {"ArXiv": "2001.00001"}
                        }
                    ]
                },
                {
                    "paperId": "p2",
                    "title": "Paper 2",
                    "externalIds": {"ArXiv": "2504.17524"},
                    "referenceCount": 0,
                    "references": []
                }
            ]
            return ApiResponse(200, {}, json.dumps(payload).encode("utf-8"))

        class FakeArxivDates:
            def submission_dates(self, ids):
                return {i: "2020-01-01T00:00:00Z" for i in ids}

        client = BatchSemanticScholarClient(api_key="synthetic", delay=0.0, transport=fake_transport, sleep=lambda _: None)
        arxiv_dates = FakeArxivDates()

        tasks = [
            {"owner": "trong", "arxiv_id": "2504.17523"},
            {"owner": "trong", "arxiv_id": "2504.17524"}
        ]

        count = process_batch_references(self.root, "test-run", tasks, client, arxiv_dates)
        self.assertEqual(count, 2)

        # Check paper 1
        p1_dir = self.root / "raw" / "trong" / "2504-17523"
        self.assertTrue((p1_dir / "references.json").is_file())
        self.assertTrue((p1_dir / "raw_references.json").is_file())
        refs1 = json.loads((p1_dir / "references.json").read_text())
        self.assertIn("2001.00001", refs1)
        self.assertEqual(refs1["2001.00001"]["submission_date"], "2020-01-01T00:00:00Z")

        # Check paper 2 (empty references)
        p2_dir = self.root / "raw" / "trong" / "2504-17524"
        self.assertTrue((p2_dir / "references.json").is_file())
        refs2 = json.loads((p2_dir / "references.json").read_text())
        self.assertEqual(refs2, {})


if __name__ == "__main__":
    unittest.main()
