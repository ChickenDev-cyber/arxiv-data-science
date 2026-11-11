"""Unit and integration tests for the parsing CLI entrypoint."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from arxiv_pipeline.parsing.cli import build_parser, main


class TestParsingCLI(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.corpus_path = self.base_path / "corpus"
        self.db_path = self.base_path / "cli_test.sqlite"
        self.bench_path = self.base_path / "benchmark.json"

        # Create 2 mock papers
        for aid in ["2409.03594", "2409.03595"]:
            paper_dir = self.corpus_path / aid
            src_dir = paper_dir / "sources" / "v1"
            src_dir.mkdir(parents=True, exist_ok=True)
            (src_dir / "doc.tex").write_text(
                rf"\documentclass{{article}}\begin{{document}}Testing CLI for {aid}.\end{{document}}",
                encoding="utf-8",
            )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_cli_execution(self):
        """Review Focus 5: CLI executes without error, produces db and benchmark report."""
        cmd = [
            "python3",
            "-m",
            "arxiv_pipeline.parsing.cli",
            "--corpus",
            str(self.corpus_path),
            "--db",
            str(self.db_path),
            "--benchmark",
            str(self.bench_path),
        ]
        env = os.environ.copy()
        env["PYTHONPATH"] = "src"
        res = subprocess.run(cmd, env=env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"CLI error: {res.stderr}")

        self.assertTrue(self.db_path.exists())
        self.assertTrue(self.bench_path.exists())

        with open(self.bench_path, "r", encoding="utf-8") as f:
            bench = json.load(f)
            self.assertEqual(bench["total_publications"], 2)
            self.assertIn("peak_ram_mb", bench)

        # Verify stdout is valid JSON
        stdout_json = json.loads(res.stdout)
        self.assertEqual(stdout_json["total_publications"], 2)

    def test_build_parser_defaults(self):
        parser = build_parser()
        args = parser.parse_args(["--corpus", "mock_dir"])
        self.assertEqual(args.corpus, "mock_dir")
        self.assertEqual(args.db, "data/m2_parsing.sqlite")
        self.assertIsNone(args.limit)
        self.assertEqual(args.batch_size, 100)
        self.assertIsNone(args.benchmark)
        self.assertIsNone(args.parquet_dir)
        self.assertFalse(args.verbose)

    def test_main_function_direct(self):
        parquet_dir = self.base_path / "parquet"
        stdout_capture = io.StringIO()
        with patch("sys.stdout", stdout_capture):
            exit_code = main([
                "--corpus",
                str(self.corpus_path),
                "--db",
                str(self.db_path),
                "--benchmark",
                str(self.bench_path),
                "--parquet-dir",
                str(parquet_dir),
                "--limit",
                "1",
                "--batch-size",
                "1",
                "--verbose",
            ])
        self.assertEqual(exit_code, 0)
        self.assertTrue(self.db_path.exists())
        self.assertTrue(self.bench_path.exists())

        out_data = json.loads(stdout_capture.getvalue())
        self.assertEqual(out_data["total_publications"], 1)
        self.assertEqual(out_data["processed_publications"], 1)

    def test_cli_missing_corpus_fails(self):
        cmd = ["python3", "-m", "arxiv_pipeline.parsing.cli"]
        env = os.environ.copy()
        env["PYTHONPATH"] = "src"
        res = subprocess.run(cmd, env=env, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("--corpus", res.stderr)

    def test_main_with_parquet_mock(self):
        parquet_dir = self.base_path / "parquet"
        stdout_capture = io.StringIO()
        with patch("arxiv_pipeline.parsing.cli.ParsingDatabase") as mock_db_cls, patch(
            "sys.stdout", stdout_capture
        ):
            mock_inst = MagicMock()
            mock_inst.__enter__.return_value = mock_inst
            mock_inst.export_to_parquet.return_value = {"elements": 10, "hierarchy": 5, "bibtex": 2}
            mock_db_cls.return_value = mock_inst

            exit_code = main([
                "--corpus",
                str(self.corpus_path),
                "--db",
                str(self.db_path),
                "--parquet-dir",
                str(parquet_dir),
            ])
            self.assertEqual(exit_code, 0)
            mock_inst.export_to_parquet.assert_called_once_with(str(parquet_dir))

        out_data = json.loads(stdout_capture.getvalue())
        self.assertEqual(out_data["parquet_export"], {"elements": 10, "hierarchy": 5, "bibtex": 2})


if __name__ == "__main__":
    unittest.main()
