"""Benchmark measurement and execution orchestrator."""

from __future__ import annotations

import json
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Literal

from ..contracts import Benchmark, Scope, SCHEMA_VERSION
from .profiler import ResourceSampler, get_dir_size, get_environment_info


class BenchmarkService:
    """Service to measure resource usage (wall time, peak/mean RAM, disk footprint) of pipeline runs."""

    def __init__(self, default_interval: float = 0.1):
        self.default_interval = default_interval

    def _load_run_scope(self, output_root: Path, run_id: str) -> tuple[Scope, bool]:
        """Attempt to read scope and is_mock flag from run directory."""
        run_dir = output_root / "runs" / run_id
        if not run_dir.exists() and (output_root / "scope.json").exists():
            run_dir = output_root

        scope_file = run_dir / "scope.json"
        if scope_file.exists():
            try:
                content = json.loads(scope_file.read_text(encoding="utf-8"))
                is_mock = content.get("is_mock", True)
                raw_scope = content.get("scope", content)
                return raw_scope, is_mock
            except Exception:
                pass

        # Default synthetic scope if not found
        fallback_scope: Scope = {
            "ranges": [],
            "paper_tasks": [],
            "discovery_complete": False,
            "versions_complete": False,
            "owner_count": 0,
            "paper_count": 0,
        }
        return fallback_scope, True

    def build_benchmark(
        self,
        run_id: str,
        output_root: Path,
        wall_seconds: float,
        exit_code: int = 0,
        cache_mode: Literal["cold", "warm", "mixed"] = "cold",
        peak_ram_bytes: int | None = None,
        mean_ram_bytes: int | None = None,
        peak_disk_bytes: int | None = None,
        sampling_interval_seconds: float = 0.1,
        method: str = "tracemalloc",
    ) -> Benchmark:
        """Construct a validated Schema 1.2 Benchmark record."""
        scope, is_mock = self._load_run_scope(output_root, run_id)
        final_output_bytes = get_dir_size(output_root / "runs" / run_id)
        if final_output_bytes == 0:
            final_output_bytes = get_dir_size(output_root)

        if peak_disk_bytes is None or peak_disk_bytes < final_output_bytes:
            peak_disk_bytes = final_output_bytes

        return {
            "schema_version": SCHEMA_VERSION,
            "is_mock": is_mock,
            "run_id": run_id,
            "scope": scope,
            "environment": get_environment_info(),
            "cache_mode": cache_mode,
            "wall_seconds": round(float(wall_seconds), 4),
            "peak_ram_bytes": peak_ram_bytes,
            "peak_disk_bytes": peak_disk_bytes,
            "final_output_bytes": final_output_bytes,
            "method": method,
            "mean_ram_bytes": mean_ram_bytes,
            "sampling_interval_seconds": sampling_interval_seconds,
            "exit_code": exit_code,
        }

    def benchmark_callable(
        self,
        fn: Callable[..., Any],
        *args: Any,
        run_id: str = "callable-bench",
        output_root: Path = Path("."),
        cache_mode: Literal["cold", "warm", "mixed"] = "cold",
        interval: float | None = None,
        **kwargs: Any,
    ) -> tuple[Any, Benchmark]:
        """Execute a Python callable while sampling its CPU/RAM/Disk metrics."""
        sampling_interval = interval or self.default_interval
        sampler = ResourceSampler(target_dir=output_root, interval_seconds=sampling_interval)

        start_time = time.perf_counter()
        sampler.start()
        exit_code = 0
        result = None

        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            exit_code = 1
            raise exc
        finally:
            wall_seconds = time.perf_counter() - start_time
            peak_ram, mean_ram, peak_disk, method = sampler.stop()

        bench = self.build_benchmark(
            run_id=run_id,
            output_root=output_root,
            wall_seconds=wall_seconds,
            exit_code=exit_code,
            cache_mode=cache_mode,
            peak_ram_bytes=peak_ram,
            mean_ram_bytes=mean_ram,
            peak_disk_bytes=peak_disk,
            sampling_interval_seconds=sampling_interval,
            method=method,
        )
        return result, bench

    def benchmark_command(
        self,
        cmd: str | list[str],
        run_id: str,
        output_root: Path = Path("."),
        cache_mode: Literal["cold", "warm", "mixed"] = "cold",
        interval: float | None = None,
    ) -> Benchmark:
        """Run an external CLI command as a subprocess while monitoring system resources."""
        sampling_interval = interval or self.default_interval
        cmd_args = shlex.split(cmd) if isinstance(cmd, str) else cmd

        start_time = time.perf_counter()
        proc = subprocess.Popen(cmd_args)
        sampler = ResourceSampler(
            target_dir=output_root,
            interval_seconds=sampling_interval,
            process_pid=proc.pid,
        )
        sampler.start()

        try:
            exit_code = proc.wait()
        finally:
            wall_seconds = time.perf_counter() - start_time
            peak_ram, mean_ram, peak_disk, method = sampler.stop()

        return self.build_benchmark(
            run_id=run_id,
            output_root=output_root,
            wall_seconds=wall_seconds,
            exit_code=exit_code,
            cache_mode=cache_mode,
            peak_ram_bytes=peak_ram,
            mean_ram_bytes=mean_ram,
            peak_disk_bytes=peak_disk,
            sampling_interval_seconds=sampling_interval,
            method=method,
        )

    def save_benchmark(self, benchmark_data: Benchmark, output_file: Path) -> Path:
        """Save benchmark JSON data to file."""
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(benchmark_data, f, indent=2, ensure_ascii=False)
        return output_file
