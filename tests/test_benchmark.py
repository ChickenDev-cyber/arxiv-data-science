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
            # Size must NOT double count the symlinked file
            self.assertEqual(base_size, size_with_link)
        except (OSError, NotImplementedError):
            pass

    def test_resource_sampler_process_tree(self):
        """Resource sampler measures process tree RSS, never whole-machine RAM or tracemalloc."""
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

    def test_benchmark_callable_exit_code_and_interrupt(self):
        """Exit codes (including 2 and 130) are preserved and benchmarks are persisted on interrupt."""
        service = BenchmarkService(default_interval=0.02)
        out_dir = self.test_root / "benchmarks_out"

        # 1. Normal run
        def normal_task():
            return 42

        res, bench = service.benchmark_callable(
            normal_task,
            run_id="bench-normal",
            output_root=self.test_root,
            output_dir=out_dir,
        )
        self.assertEqual(res, 42)
        self.assertEqual(bench["exit_code"], 0)

        # 2. SystemExit(2)
        def exit_code_2_task():
            raise SystemExit(2)

        with self.assertRaises(SystemExit) as ctx:
            service.benchmark_callable(
                exit_code_2_task,
                run_id="bench-exit-2",
                output_root=self.test_root,
                output_dir=out_dir,
            )
        self.assertEqual(ctx.exception.code, 2)
        # Check saved benchmark file
        bench_file_2 = out_dir / "bench-exit-2.json"
        self.assertTrue(bench_file_2.exists())
        saved_2 = json.loads(bench_file_2.read_text(encoding="utf-8"))
        self.assertEqual(saved_2["exit_code"], 2)

        # 3. KeyboardInterrupt (Ctrl+C) -> exit code 130 and benchmark saved
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

    def test_timings_failed_retries_and_complete_output(self):
        """Extract timings from events.jsonl to include retries/failed attempts, separated from wall time."""
        logs_dir = self.test_root / "logs" / "timing-test"
        logs_dir.mkdir(parents=True, exist_ok=True)

        events = [
            {"step": "discovery", "duration_seconds": 2.5, "status": "success"},
            # failed attempt for paper 2501.00001 (0.5s), followed by retry success (1.2s)
            {"step": "metadata", "arxiv_id": "2501.00001", "duration_seconds": 0.5, "status": "failed"},
            {"step": "metadata", "arxiv_id": "2501.00001", "duration_seconds": 1.2, "status": "success"},
            {"step": "sources", "arxiv_id": "2501.00001", "duration_seconds": 0.8, "status": "success"},
            {"step": "metadata", "arxiv_id": "2501.00002", "duration_seconds": 1.0, "status": "success"},
        ]
        with open(logs_dir / "events.jsonl", "w", encoding="utf-8") as f:
            for item in events:
                f.write(json.dumps(item) + "\n")

        timings = extract_run_timings(self.test_root, "timing-test")
        self.assertEqual(timings["discovery_seconds"], 2.5)
        # 2501.00001 total duration: 0.5 (failed) + 1.2 (retry) + 0.8 (source) = 2.5s
        self.assertEqual(timings["per_paper_seconds"]["2501.00001"], 2.5)
        self.assertEqual(timings["per_paper_seconds"]["2501.00002"], 1.0)
        self.assertEqual(timings["mean_paper_seconds"], 1.75)

        bytes_count = compute_complete_output_bytes(self.test_root, "timing-test")
        self.assertGreater(bytes_count, 0)

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
