"""Offline unit tests for local_source_runner."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from arxiv_pipeline.contracts import paper_folder
from arxiv_pipeline.sources.local_source_runner import (
    detect_run_id,
    get_assigned_papers,
    get_pending_metadata_tasks,
    get_pending_source_tasks,
)


class LocalSourceRunnerTests(unittest.TestCase):
    def test_detect_run_id_and_pending_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            corpus_dir = Path(tmp)
            db_path = corpus_dir / "state.sqlite"
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("CREATE TABLE runs (run_id TEXT, initial_scope TEXT)")
            c.execute("INSERT INTO runs VALUES ('m1-test-01', NULL)")

            c.execute(
                "CREATE TABLE run_tasks (run_id TEXT, task_key TEXT, input_json TEXT, latest_attempt_id INTEGER, accepted_attempt_id INTEGER)"
            )
            c.execute("CREATE TABLE attempts (attempt_id INTEGER, run_id TEXT, task_key TEXT, result_json TEXT, result_valid INTEGER)")

            # Discovery attempt with 2 assigned papers
            discovery_data = {
                "step": "discovery",
                "status": "success",
                "data": {
                    "papers": [
                        {"owner": "trong", "arxiv_id": "2504.00001"},
                        {"owner": "trong", "arxiv_id": "2504.00002"},
                        {"owner": "trong", "arxiv_id": "2504.00003"},
                    ]
                },
            }
            c.execute(
                "INSERT INTO attempts VALUES (10, 'm1-test-01', '[\"trong\",\"discovery\"]', ?, 1)",
                (json.dumps(discovery_data),),
            )

            # Metadata tasks:
            # Paper 1: accepted
            c.execute(
                "INSERT INTO run_tasks VALUES ('m1-test-01', '[\"trong\",\"metadata\",\"2504.00001\",null]', '{\"owner\":\"trong\",\"arxiv_id\":\"2504.00001\"}', 11, 11)"
            )
            c.execute("INSERT INTO attempts VALUES (11, 'm1-test-01', '[\"trong\",\"metadata\",\"2504.00001\",null]', '{\"status\": \"success\"}', 1)")
            # Fake metadata.json on disk for paper 1
            meta_p1 = corpus_dir / "raw" / "trong" / paper_folder("2504.00001") / "metadata.json"
            meta_p1.parent.mkdir(parents=True, exist_ok=True)
            meta_p1.write_text("{}", encoding="utf-8")

            # Paper 2: pending (no metadata.json on disk)
            c.execute(
                "INSERT INTO run_tasks VALUES ('m1-test-01', '[\"trong\",\"metadata\",\"2504.00002\",null]', '{\"owner\":\"trong\",\"arxiv_id\":\"2504.00002\"}', 12, NULL)"
            )
            c.execute("INSERT INTO attempts VALUES (12, 'm1-test-01', '[\"trong\",\"metadata\",\"2504.00002\",null]', '{\"status\": \"failed\"}', 0)")

            # Paper 3: not yet in run_tasks, no file -> pending

            # Sources tasks:
            # Task 1: pending
            c.execute(
                "INSERT INTO run_tasks VALUES ('m1-test-01', '[\"trong\",\"sources\",\"2504.00001\",1]', '{\"owner\":\"trong\",\"arxiv_id\":\"2504.00001\",\"version\":1}', 1, NULL)"
            )
            c.execute("INSERT INTO attempts VALUES (1, 'm1-test-01', '[\"trong\",\"sources\",\"2504.00001\",1]', '{\"status\": \"failed\"}', 0)")

            # Task 2: accepted
            c.execute(
                "INSERT INTO run_tasks VALUES ('m1-test-01', '[\"trong\",\"sources\",\"2504.00002\",1]', '{\"owner\":\"trong\",\"arxiv_id\":\"2504.00002\",\"version\":1}', 2, 2)"
            )
            c.execute("INSERT INTO attempts VALUES (2, 'm1-test-01', '[\"trong\",\"sources\",\"2504.00002\",1]', '{\"status\": \"success\"}', 1)")

            # Task 3: not_found (should be skipped when skip_not_found=True)
            c.execute(
                "INSERT INTO run_tasks VALUES ('m1-test-01', '[\"trong\",\"sources\",\"2504.00003\",1]', '{\"owner\":\"trong\",\"arxiv_id\":\"2504.00003\",\"version\":1}', 3, NULL)"
            )
            c.execute("INSERT INTO attempts VALUES (3, 'm1-test-01', '[\"trong\",\"sources\",\"2504.00003\",1]', '{\"status\": \"not_found\"}', 0)")

            conn.commit()
            conn.close()

            # Test detect_run_id
            detected = detect_run_id(db_path)
            self.assertEqual(detected, "m1-test-01")

            # Test get_assigned_papers
            assigned = get_assigned_papers(corpus_dir, db_path, "m1-test-01")
            self.assertEqual(len(assigned), 3)

            # Test get_pending_metadata_tasks
            meta_pending = get_pending_metadata_tasks(corpus_dir, db_path, "m1-test-01")
            self.assertEqual([p["arxiv_id"] for p in meta_pending], ["2504.00002", "2504.00003"])

            # Test get_pending_source_tasks
            tasks = get_pending_source_tasks(db_path, "m1-test-01", skip_not_found=True)
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]["arxiv_id"], "2504.00001")


if __name__ == "__main__":
    unittest.main()
