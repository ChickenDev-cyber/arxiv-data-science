"""Offline unit tests for local_source_runner."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from arxiv_pipeline.sources.local_source_runner import detect_run_id, get_pending_source_tasks


class LocalSourceRunnerTests(unittest.TestCase):
    def test_detect_run_id_and_pending_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state.sqlite"
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("CREATE TABLE runs (run_id TEXT)")
            c.execute("INSERT INTO runs VALUES ('m1-test-01')")

            c.execute("CREATE TABLE run_tasks (run_id TEXT, task_key TEXT, input_json TEXT, latest_attempt_id INTEGER, accepted_attempt_id INTEGER)")
            c.execute("CREATE TABLE attempts (attempt_id INTEGER, result_json TEXT)")

            # Task 1: pending
            c.execute("INSERT INTO run_tasks VALUES ('m1-test-01', '[\"trong\",\"sources\",\"2504.00001\",1]', '{\"owner\":\"trong\",\"arxiv_id\":\"2504.00001\",\"version\":1}', 1, NULL)")
            c.execute("INSERT INTO attempts VALUES (1, '{\"status\": \"failed\"}')")

            # Task 2: accepted
            c.execute("INSERT INTO run_tasks VALUES ('m1-test-01', '[\"trong\",\"sources\",\"2504.00002\",1]', '{\"owner\":\"trong\",\"arxiv_id\":\"2504.00002\",\"version\":1}', 2, 2)")
            c.execute("INSERT INTO attempts VALUES (2, '{\"status\": \"success\"}')")

            # Task 3: not_found (should be skipped)
            c.execute("INSERT INTO run_tasks VALUES ('m1-test-01', '[\"trong\",\"sources\",\"2504.00003\",1]', '{\"owner\":\"trong\",\"arxiv_id\":\"2504.00003\",\"version\":1}', 3, NULL)")
            c.execute("INSERT INTO attempts VALUES (3, '{\"status\": \"not_found\"}')")

            conn.commit()
            conn.close()

            detected = detect_run_id(db_path)
            self.assertEqual(detected, "m1-test-01")

            tasks = get_pending_source_tasks(db_path, "m1-test-01", skip_not_found=True)
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]["arxiv_id"], "2504.00001")


if __name__ == "__main__":
    unittest.main()

