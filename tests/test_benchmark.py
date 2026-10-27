import time
from pathlib import Path
import unittest

from arxiv_pipeline.contracts import SCHEMA_VERSION
from arxiv_pipeline.benchmark.profiler import (
    ResourceSampler,
    get_dir_size,
    get_environment_info,
)
from arxiv_pipeline.benchmark.runner import BenchmarkService
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

    def test_dir_size(self):
        size = get_dir_size(self.test_root)
        self.assertGreater(size, 0)

    def test_resource_sampler(self):
        sampler = ResourceSampler(target_dir=self.test_root, interval_seconds=0.02)
        sampler.start()
        time.sleep(0.06)
        peak_ram, mean_ram, peak_disk, method = sampler.stop()

        self.assertGreater(peak_ram, 0)
        self.assertIsNotNone(mean_ram)
        self.assertGreaterEqual(mean_ram, 0)
        self.assertGreaterEqual(peak_disk, 0)
        self.assertIn(method, ("psutil_rss", "tracemalloc"))

    def test_benchmark_callable(self):
        service = BenchmarkService(default_interval=0.02)

        def dummy_task():
            time.sleep(0.05)
            return [i * 2 for i in range(1000)]

        result, bench = service.benchmark_callable(
            dummy_task,
            run_id="test-bench-001",
            output_root=self.test_root,
        )

        self.assertEqual(len(result), 1000)
        self.assertEqual(bench["schema_version"], SCHEMA_VERSION)
        self.assertEqual(bench["run_id"], "test-bench-001")
        self.assertGreater(bench["wall_seconds"], 0.04)
        self.assertEqual(bench["exit_code"], 0)
        self.assertGreater(bench["peak_ram_bytes"], 0)
        self.assertIsNotNone(bench["mean_ram_bytes"])

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
