"""Regression tests for deprecated --aggregate-runs CLI behavior."""

import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import tempfile
import unittest

from arxiv_pipeline.statistics.cli import main as cli_main, parse_args


class StatisticsCliLegacyTests(unittest.TestCase):
    def test_aggregate_runs_rejected_early_with_code_1_and_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "stats"
            out_dir.mkdir()
            sentinel = out_dir / "sentinel.txt"
            sentinel.write_text("preserve", encoding="utf-8")

            # Point to a nonexistent output root to prove rejection happens before I/O
            nonexistent_root = Path(tmp) / "does_not_exist"
            stderr = io.StringIO()
            stdout = io.StringIO()
            with redirect_stderr(stderr), redirect_stdout(stdout):
                code = cli_main([
                    "--output-root", str(nonexistent_root),
                    "--aggregate-runs", "run1", "run2",
                    "--output-dir", str(out_dir),
                ])

            self.assertEqual(code, 1)
            err_output = stderr.getvalue()
            self.assertIn("--collection", err_output)
            self.assertIn("deprecated", err_output.lower())
            # Ensure no stats output was written and sentinel preserved
            created_files = [p.name for p in out_dir.iterdir()]
            self.assertEqual(created_files, ["sentinel.txt"])

    def test_aggregate_runs_rejected_even_when_combined_with_collection(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "stats"
            out_dir.mkdir()

            fake_col = Path(tmp) / "nonexistent_collection"
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                code = cli_main([
                    "--collection", str(fake_col),
                    "--aggregate-runs", "run1",
                    "--output-dir", str(out_dir),
                ])

            self.assertEqual(code, 1)
            self.assertIn("--collection", stderr.getvalue())
            self.assertIn("deprecated", stderr.getvalue().lower())
            self.assertEqual(list(out_dir.iterdir()), [])

    def test_aggregate_runs_rejected_when_combined_with_multiple_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "stats"
            out_dir.mkdir()

            stderr = io.StringIO()
            with redirect_stderr(stderr):
                code = cli_main([
                    "--output-roots", str(Path(tmp) / "root1"), str(Path(tmp) / "root2"),
                    "--aggregate-runs", "run1", "run2",
                    "--output-dir", str(out_dir),
                ])

            self.assertEqual(code, 1)
            self.assertIn("--collection", stderr.getvalue())
            self.assertIn("deprecated", stderr.getvalue().lower())
            self.assertEqual(list(out_dir.iterdir()), [])

    def test_aggregate_runs_help_mentions_deprecated_and_collection(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout), self.assertRaises(SystemExit):
            parse_args(["--help"])
        help_text = stdout.getvalue()
        self.assertIn("--aggregate-runs", help_text)
        self.assertIn("deprecated", help_text.lower())
        self.assertIn("--collection", help_text)


if __name__ == "__main__":
    unittest.main()
