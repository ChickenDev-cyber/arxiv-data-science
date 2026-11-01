"""Regression cases from the T6 integration review; no network access."""

from contextlib import redirect_stderr, redirect_stdout
import ast
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from arxiv_pipeline.benchmark.cli import main as benchmark_main
from arxiv_pipeline.benchmark.runner import BenchmarkService
from arxiv_pipeline.contracts import Benchmark
from arxiv_pipeline.runner.mock import make_mock_modules
from arxiv_pipeline.runner.models import RunOptions
from arxiv_pipeline.runner.service import run_pipeline
from arxiv_pipeline.statistics.cli import main as statistics_main
from arxiv_pipeline.statistics.service import StatisticsService
from arxiv_pipeline.validation.results import _check

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class BenchmarkTimingDisplayTests(unittest.TestCase):
    def test_measured_benchmark_matches_the_shared_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, record = BenchmarkService().benchmark_callable(
                lambda: 0, run_id="contract", output_root=root / "corpus",
                output_dir=root / "measurements",
            )
            self.assertEqual(_check(record, Benchmark, ""), [])

    def test_cli_displays_measured_sidecar_timings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # An external producer writes deterministic invocation events. The
            # benchmark must read its real sidecar, not a mocked service result.
            script = """
import json, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
now = datetime.now(timezone.utc)
def stamp(offset):
    return (now + timedelta(seconds=offset)).isoformat().replace('+00:00', 'Z')
events = [
    {'step': 'discovery', 'arxiv_id': None, 'started_at': stamp(0),
     'finished_at': stamp(1), 'duration_seconds': 1},
    {'step': 'metadata', 'arxiv_id': '2504.17523', 'started_at': stamp(1),
     'finished_at': stamp(4), 'duration_seconds': 3},
]
path = Path(sys.argv[1]) / 'logs/display/events.jsonl'
path.parent.mkdir(parents=True)
path.write_text(''.join(json.dumps(event) + '\\n' for event in events))
"""
            command = shlex.join([sys.executable, "-c", script, str(root / "corpus")])
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = benchmark_main([
                    "--cmd", command, "--run-id", "display",
                    "--output-root", str(root / "corpus"),
                    "--output-dir", str(root / "measurements"),
                ])
            self.assertEqual(code, 0, stderr.getvalue())
            timing = json.loads((root / "measurements/display-timing.json").read_text())
            self.assertEqual(timing["discovery_seconds"], 1.0)
            self.assertEqual(timing["mean_paper_seconds"], 3.0)
            self.assertIn("Discovery Time: 1.00s", stdout.getvalue())
            self.assertIn("Mean Paper Time: 3.00s", stdout.getvalue())

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux process supervision")
    def test_terminal_interrupt_stops_descendants_before_benchmark_returns(self):
        self._assert_terminal_interrupt_cleanup(detached=False)

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux process supervision")
    def test_terminal_interrupt_also_stops_descendants_in_another_session(self):
        self._assert_terminal_interrupt_cleanup(detached=True)

    def _assert_terminal_interrupt_cleanup(self, detached):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
            probe = subprocess.run(
                [sys.executable, str(PROJECT_ROOT / "tests/fixtures/benchmark_interrupt_probe.py"),
                 tmp, str(int(detached))],
                env=env, capture_output=True, text=True, timeout=40,
            )
            self.assertEqual(probe.returncode, 0, probe.stderr)
            report = json.loads(probe.stdout)
            self.assertEqual(report["wrapper_exit"], 130, report["stderr"])
            self.assertEqual(report["benchmark_exit"], 130)
            self.assertFalse(report["grandchild_running"], "benchmark returned while a descendant was still running")


class StatisticsOutputCollisionTests(unittest.TestCase):
    def test_duplicate_run_ids_are_rejected_before_any_output_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            roots = []
            for owner in ("hoang", "trong"):
                corpus = root / owner
                roots.append(corpus)
                options = RunOptions("baseline-001", corpus, (owner,), True)
                run_pipeline(options, make_mock_modules(options))
            output = root / "statistics"
            output.mkdir()
            existing = output / "baseline-001.json"
            existing.write_text("keep the previous statistics\n")
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = statistics_main([
                    "--output-roots", *map(str, roots), "--output-dir", str(output),
                ])
            self.assertEqual(code, 1)
            self.assertIn("baseline-001", stderr.getvalue())
            self.assertEqual(existing.read_text(), "keep the previous statistics\n")
            self.assertNotIn("[SUCCESS]", stdout.getvalue())


class ReportMissingMeasurementsTests(unittest.TestCase):
    def test_notebook_and_generator_display_null_and_zero_rates(self):
        notebook = json.loads((PROJECT_ROOT / "report.ipynb").read_text())
        notebook_cell = "".join(next(c["source"] for c in notebook["cells"] if c["cell_type"] == "code"))
        generator = ast.parse((PROJECT_ROOT / "reports/create_notebook.py").read_text())
        # Execute the generator's actual cell text without requiring nbformat
        # or a plotting backend just to test the summary text.
        generator_cell = next(
            ast.literal_eval(node.args[0]) for node in ast.walk(generator)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "new_code_cell"
        )
        for stop_after, expected in ((1, "N/A"), (2, "0.0%")):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                options = RunOptions("paused", root / "corpus", ("trong",), True)
                run_pipeline(options, make_mock_modules(options), stop_after_tasks=stop_after)
                stats = StatisticsService().build_statistics(root / "corpus", "paused")
                (root / "statistics").mkdir()
                (root / "benchmarks").mkdir()
                (root / "statistics/baseline-001.json").write_text(json.dumps(stats))
                (root / "benchmarks/baseline-001.json").write_text("{}")
                for name, code in (("notebook", notebook_cell), ("generator", generator_cell)):
                    with self.subTest(artifact=name, stop_after=stop_after):
                        matplotlib = types.ModuleType("matplotlib")
                        matplotlib.__path__ = []
                        pyplot = types.ModuleType("matplotlib.pyplot")
                        matplotlib.pyplot = pyplot
                        output = io.StringIO()
                        original_cwd, original_path = Path.cwd(), sys.path[:]
                        try:
                            os.chdir(root)
                            with patch.dict(sys.modules, {"matplotlib": matplotlib, "matplotlib.pyplot": pyplot}):
                                with redirect_stdout(output):
                                    exec(compile(code, name, "exec"), {})
                        finally:
                            os.chdir(original_cwd)
                            sys.path[:] = original_path
                        self.assertIn(f"Tỷ lệ thành công thu thập nguồn: {expected}", output.getvalue())
