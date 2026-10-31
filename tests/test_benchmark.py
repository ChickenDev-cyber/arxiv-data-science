import json
import time
from pathlib import Path
import unittest

from arxiv_pipeline.contracts import SCHEMA_VERSION
from arxiv_pipeline.benchmark.profiler import (
    ResourceSampler,
    get_dir_size,
    get_environment_info,
    get_pipeline_process_ram,
)
from arxiv_pipeline.benchmark.runner import (
    BenchmarkService,
    compute_complete_output_bytes,
    extract_run_timings,
)
from arxiv_pipeline.benchmark.cli import main as cli_main
from arxiv_pipeline.runner.models import RunOutcome, RunScope


class TestBenchmark(unittest.TestCase):
    def setUp(self):
        self.test_root = Path("tests/fixtures/test_bench_run")
        self.test_root.mkdir(parents=True, exist_ok=True)
        (self.test_root / "sample.txt").write_text("Hello benchmark" * 100, encoding="utf-8")

    def tearDown(self):
        import shutil
        if self.test_root.exists():
            shutil.rmtree(self.test_root, ignore_errors=True)

    def test_environment_info(self):
        env = get_environment_info()
        self.assertIn("os", env)
        self.assertIn("architecture", env)
        self.assertIn("python_version", env)
        self.assertIn("cpu_count", env)

    def test_dir_size_and_symlinks(self):
        """Directory size must include subfolders, staging/scratch, and never follow symlinks."""
        staging_dir = self.test_root / "staging"
        staging_dir.mkdir(parents=True, exist_ok=True)
        (staging_dir / "stage_file.bin").write_bytes(b"x" * 2048)

        scratch_dir = self.test_root / "scratch"
        scratch_dir.mkdir(parents=True, exist_ok=True)
        (scratch_dir / "scratch_file.bin").write_bytes(b"y" * 1024)

        base_size = get_dir_size(self.test_root)
        self.assertGreaterEqual(base_size, 3072)

        # Create symlink if platform supports it
        symlink_path = self.test_root / "link_to_file"
        try:
            symlink_path.symlink_to(staging_dir / "stage_file.bin")
            size_with_link = get_dir_size(self.test_root)
            self.assertEqual(base_size, size_with_link)
        except (OSError, NotImplementedError):
            pass

    def test_resource_sampler_process_tree(self):
        """Resource sampler measures process tree RSS (wrapper + runner), never whole-machine RAM or tracemalloc."""
        sampler = ResourceSampler(target_dir=self.test_root, interval_seconds=0.02)
        sampler.start()
        time.sleep(0.06)
        peak_ram, mean_ram, peak_disk, method = sampler.stop()

        self.assertIn(method, ("process_tree_rss (psutil)", "process_rss (/proc)", "unsupported"))
        if method != "unsupported":
            self.assertIsNotNone(peak_ram)
            self.assertGreater(peak_ram, 0)
            self.assertIsNotNone(mean_ram)
            self.assertGreaterEqual(mean_ram, 0)
        self.assertGreaterEqual(peak_disk, 0)

    def test_time_weighted_mean_ram(self):
        """Mean RAM calculation uses trapezoidal time-weighted integration."""
        sampler = ResourceSampler(interval_seconds=0.1)
        # Mock samples: (time, ram)
        # 0s to 1s: RAM = 100MB
        # 1s to 2s: RAM = 200MB
        # Trapezoidal area: (100+200)/2 * 1s = 150MB*s
        # Total duration: 2s -> weighted mean: 150MB
        sampler.ram_samples = [
            (10.0, 100 * 1024 * 1024),
            (11.0, 100 * 1024 * 1024),
            (12.0, 200 * 1024 * 1024),
        ]
        sampler._sample_once = lambda: None  # avoid overriding manual samples in stop()
        _, mean_ram, _, _ = sampler.stop()
        self.assertIsNotNone(mean_ram)
        # Area: (100+100)/2 * 1 + (100+200)/2 * 1 = 100 + 150 = 250 MB*s over 2s -> 125 MB
        expected_mb = 125
        self.assertEqual(round(mean_ram / (1024 * 1024)), expected_mb)

    def test_callable_returns_none_stops_sampler_and_saves_benchmark(self):
        """When callable returns None, sampler must stop and benchmark record must be completed and saved."""
        service = BenchmarkService(default_interval=0.02)
        out_dir = self.test_root / "bench_none_out"

        def none_task():
            time.sleep(0.03)
            return None

        result, bench = service.benchmark_callable(
            none_task,
            run_id="bench-none",
            output_root=self.test_root,
            output_dir=out_dir,
        )
        self.assertIsNone(result)
        self.assertEqual(bench["exit_code"], 0)
        self.assertGreater(bench["wall_seconds"], 0.02)
        saved_file = out_dir / "bench-none.json"
        self.assertTrue(saved_file.exists())

    def test_callable_preserves_runoutcome_exit_code_2(self):
        """When callable returns a RunOutcome with exit_code=2, it must NOT be converted to 0."""
        service = BenchmarkService(default_interval=0.02)
        out_dir = self.test_root / "bench_outcome_out"

        mock_scope: RunScope = {
            "schema_version": SCHEMA_VERSION,
            "run_id": "bench-outcome-2",
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
        outcome = RunOutcome(
            exit_code=2,
            scope=mock_scope,
            executed_tasks=0,
            reused_tasks=0,
            validation={"valid": False, "issues": []},
            quality={"valid": False, "issues": []},
        )

        def outcome_task():
            return outcome

        res, bench = service.benchmark_callable(
            outcome_task,
            run_id="bench-outcome-2",
            output_root=self.test_root,
            output_dir=out_dir,
        )
        self.assertEqual(res.exit_code, 2)
        self.assertEqual(bench["exit_code"], 2)  # Strictly preserved, not coerced to 0!
        saved = json.loads((out_dir / "bench-outcome-2.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["exit_code"], 2)

    def test_benchmark_callable_interrupt_130(self):
        """KeyboardInterrupt records exit code 130 and saves benchmark file."""
        service = BenchmarkService(default_interval=0.02)
        out_dir = self.test_root / "bench_interrupt_out"

        def interrupt_task():
            raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            service.benchmark_callable(
                interrupt_task,
                run_id="bench-ctrl-c",
                output_root=self.test_root,
                output_dir=out_dir,
            )
        bench_file_c = out_dir / "bench-ctrl-c.json"
        self.assertTrue(bench_file_c.exists())
        saved_c = json.loads(bench_file_c.read_text(encoding="utf-8"))
        self.assertEqual(saved_c["exit_code"], 130)

    def test_benchmark_has_no_timing_field(self):
        """Benchmark must strictly adhere to the 14 Schema 1.2 fields and NOT include a timing field."""
        service = BenchmarkService()
        bench = service.build_benchmark(
            run_id="strict-bench",
            output_root=self.test_root,
            wall_seconds=1.23,
        )
        self.assertNotIn("timing", bench)
        self.assertEqual(len(bench.keys()), 14)

    def test_timings_failed_retries_and_resume_isolation(self):
        """extract_run_timings supports since_timestamp to isolate invocation and ignore old resume data."""
        logs_dir = self.test_root / "logs" / "timing-test"
        logs_dir.mkdir(parents=True, exist_ok=True)

        events = [
            # Old invocation from yesterday
            {
                "step": "discovery",
                "duration_seconds": 10.0,
                "status": "success",
                "finished_at": "2026-10-20T00:00:00Z",
            },
            # Current invocation
            {
                "step": "discovery",
                "duration_seconds": 2.5,
                "status": "success",
                "finished_at": "2026-10-31T10:00:00Z",
            },
            {
                "step": "metadata",
                "arxiv_id": "2501.00001",
                "duration_seconds": 0.5,
                "status": "failed",
                "finished_at": "2026-10-31T10:00:05Z",
            },
            {
                "step": "metadata",
                "arxiv_id": "2501.00001",
                "duration_seconds": 1.2,
                "status": "success",
                "finished_at": "2026-10-31T10:00:10Z",
            },
        ]
        with open(logs_dir / "events.jsonl", "w", encoding="utf-8") as f:
            for item in events:
                f.write(json.dumps(item) + "\n")

        # Without timestamp filter: counts old 10s discovery + 2.5s = 12.5s
        all_timings = extract_run_timings(self.test_root, "timing-test")
        self.assertEqual(all_timings["discovery_seconds"], 12.5)

        # With since_timestamp: only current invocation
        current_timings = extract_run_timings(
            self.test_root,
            "timing-test",
            since_timestamp="2026-10-31T09:00:00Z",
        )
        self.assertEqual(current_timings["discovery_seconds"], 2.5)
        self.assertEqual(current_timings["per_paper_seconds"]["2501.00001"], 1.7)

    def test_unmeasured_numbers_as_none_not_zero(self):
        """Unmeasured values return None, never converted to 0."""
        service = BenchmarkService()
        bench = service.build_benchmark(
            run_id="unmeasured-test",
            output_root=self.test_root,
            peak_ram_bytes=None,
            mean_ram_bytes=None,
        )
        self.assertIsNone(bench["peak_ram_bytes"])
        self.assertIsNone(bench["mean_ram_bytes"])

    def test_cli_execution(self):
        out_dir = self.test_root / "bench_out"
        exit_code = cli_main([
            "--run-id", "test-bench-cli",
            "--output-root", str(self.test_root),
            "--output-dir", str(out_dir),
            "--interval", "0.05",
        ])
        self.assertEqual(exit_code, 0)
        bench_file = out_dir / "test-bench-cli.json"
        self.assertTrue(bench_file.exists())


if __name__ == "__main__":
    unittest.main()
