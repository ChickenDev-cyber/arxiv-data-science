"""Regressions for Benchmark CLI measurement requirements."""

import io
import json
import shlex
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from arxiv_pipeline.benchmark.cli import main as cli_main
from arxiv_pipeline.contracts import Benchmark
from arxiv_pipeline.validation.results import _check


class TestBenchmarkCliMeasurement(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="test_bench_cli_")
        self.test_root = Path(self.temp_dir.name) / "root"
        self.output_dir = Path(self.temp_dir.name) / "benchmarks"
        self.test_root.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_no_cmd_fails_early_and_creates_no_artifacts(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli_main([
                "--run-id", "test-no-cmd",
                "--output-root", str(self.test_root),
                "--output-dir", str(self.output_dir),
            ])
        self.assertEqual(code, 1)
        self.assertIn("--cmd", stderr.getvalue())
        self.assertFalse((self.output_dir / "test-no-cmd.json").exists())
        self.assertFalse((self.output_dir / "test-no-cmd-timing.json").exists())

    def test_blank_cmd_fails_early_and_creates_no_artifacts(self):
        for blank_cmd in ("", "   ", "\t\n"):
            with self.subTest(blank_cmd=repr(blank_cmd)):
                stdout, stderr = io.StringIO(), io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = cli_main([
                        "--run-id", "test-blank-cmd",
                        "--output-root", str(self.test_root),
                        "--output-dir", str(self.output_dir),
                        "--cmd", blank_cmd,
                    ])
                self.assertEqual(code, 1)
                self.assertIn("--cmd", stderr.getvalue())
                self.assertFalse((self.output_dir / "test-blank-cmd.json").exists())
                self.assertFalse((self.output_dir / "test-blank-cmd-timing.json").exists())

    def test_existing_artifacts_preserved_when_cmd_missing_or_blank(self):
        existing_bench = self.output_dir / "preserve-test.json"
        existing_timing = self.output_dir / "preserve-test-timing.json"
        existing_bench.write_text('{"preserved": true}', encoding="utf-8")
        existing_timing.write_text('{"preserved_timing": true}', encoding="utf-8")

        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli_main([
                "--run-id", "preserve-test",
                "--output-root", str(self.test_root),
                "--output-dir", str(self.output_dir),
                "--cmd", "  ",
            ])
        self.assertEqual(code, 1)
        self.assertEqual(existing_bench.read_text(encoding="utf-8"), '{"preserved": true}')
        self.assertEqual(existing_timing.read_text(encoding="utf-8"), '{"preserved_timing": true}')

    def test_real_measured_invocation_passes_schema_check(self):
        cmd = f"{shlex.quote(sys.executable)} -c 'import time; time.sleep(0.01)'"
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli_main([
                "--run-id", "real-cmd-run",
                "--output-root", str(self.test_root),
                "--output-dir", str(self.output_dir),
                "--cmd", cmd,
                "--interval", "0.02",
            ])
        self.assertEqual(code, 0)
        bench_file = self.output_dir / "real-cmd-run.json"
        self.assertTrue(bench_file.exists())
        data = json.loads(bench_file.read_text(encoding="utf-8"))

        issues = _check(data, Benchmark, "")
        self.assertEqual(issues, [])
        self.assertIsNotNone(data.get("wall_seconds"))
        self.assertGreater(data["wall_seconds"], 0)
        self.assertEqual(data.get("exit_code"), 0)

    def test_real_measured_invocation_preserves_exit_code_2(self):
        cmd = f"{shlex.quote(sys.executable)} -c 'import sys; sys.exit(2)'"
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli_main([
                "--run-id", "real-cmd-exit2",
                "--output-root", str(self.test_root),
                "--output-dir", str(self.output_dir),
                "--cmd", cmd,
                "--interval", "0.02",
            ])
        self.assertEqual(code, 2)
        bench_file = self.output_dir / "real-cmd-exit2.json"
        self.assertTrue(bench_file.exists())
        data = json.loads(bench_file.read_text(encoding="utf-8"))
        self.assertEqual(data.get("exit_code"), 2)

    def test_actual_empty_command_measured_zero_disk_displays_zero_not_na(self):
        """Valid measured zero disk/output must display 0.00 KB, not N/A (Not measured)."""
        empty_root = Path(self.temp_dir.name) / "empty_root"
        empty_root.mkdir(parents=True, exist_ok=True)
        cmd = f"{shlex.quote(sys.executable)} -c 'pass'"
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli_main([
                "--run-id", "test-zero-disk",
                "--output-root", str(empty_root),
                "--output-dir", str(self.output_dir),
                "--cmd", cmd,
                "--interval", "0.02",
            ])
        self.assertEqual(code, 0)
        bench_file = self.output_dir / "test-zero-disk.json"
        self.assertTrue(bench_file.exists())
        data = json.loads(bench_file.read_text(encoding="utf-8"))
        self.assertEqual(data.get("final_output_bytes"), 0)
        self.assertEqual(data.get("peak_disk_bytes"), 0)

        out = stdout.getvalue()
        # Must show 0.00 KB for final output and peak disk, NOT N/A (Not measured)
        self.assertIn("Final Output Size: 0.00 KB", out)
        self.assertIn("Peak Disk Size: 0.00 KB", out)
        self.assertNotIn("Final Output Size: N/A (Not measured)", out)
        self.assertNotIn("Peak Disk Size: N/A (Not measured)", out)

    def test_format_zero_vs_none_distinction(self):
        """Formatter check proving zero displays numeric value and None displays N/A."""
        from arxiv_pipeline.benchmark.cli import format_kilobytes, format_megabytes, format_seconds

        self.assertEqual(format_kilobytes(0), "0.00 KB")
        self.assertEqual(format_kilobytes(None), "N/A (Not measured)")

        self.assertEqual(format_megabytes(0), "0.00 MB")
        self.assertEqual(format_megabytes(None), "N/A (Not measured)")

        self.assertEqual(format_seconds(0.0), "0.00s")
        self.assertEqual(format_seconds(None), "N/A (Not measured)")


if __name__ == "__main__":
    unittest.main()
