"""Benchmark measurement and execution orchestrator.

Tracks whole-system resources (RAM, Disk) and granular task timings
(Discovery time, per-paper processing time, mean paper time).
Official benchmarks run on Google Colab CPU (Linux).
"""

from __future__ import annotations

import json
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Literal

from ..contracts import Benchmark, Scope, SCHEMA_VERSION, paper_folder
from .profiler import ResourceSampler, get_dir_size, get_environment_info, get_system_ram_used


def compute_complete_output_bytes(
    output_root: Path,
    run_id: str,
    paper_tasks: list[dict[str, Any]] | None = None,
) -> int:
    """Measure the true, comprehensive byte footprint of all generated artifacts for a run."""
    if not output_root.exists():
        return 0

    # 1. If output_root is a dedicated directory for this run (e.g. data/m1-demo or /tmp/arxiv-t1)
    # Check if output_root itself contains runs/run_id or is an isolated output tree
    run_runs_dir = output_root / "runs" / run_id
    if not run_runs_dir.exists() and (output_root / "scope.json").exists():
        return get_dir_size(output_root)

    # 2. Check individual artifacts in output_root
    total_bytes = 0
    
    # Run handoff and logs
    if run_runs_dir.exists():
        total_bytes += get_dir_size(run_runs_dir)
    run_logs_dir = output_root / "logs" / run_id
    if run_logs_dir.exists():
        total_bytes += get_dir_size(run_logs_dir)

    # Discovery folder
    discovery_dir = output_root / "discovery"
    if discovery_dir.exists():
        total_bytes += get_dir_size(discovery_dir)

    # SQLite state database
    for db_file in (
        output_root / "state.sqlite",
        output_root / "state.sqlite-wal",
        output_root / "state.sqlite-shm",
    ):
        if db_file.exists():
            try:
                total_bytes += db_file.stat().st_size
            except OSError:
                pass

    # Raw corpus (metadata, references, sources vN) for all papers in scope
    raw_dir = output_root / "raw"
    if raw_dir.exists():
        if paper_tasks:
            counted_dirs: set[Path] = set()
            for task in paper_tasks:
                owner = task.get("owner")
                aid = task.get("arxiv_id")
                if owner and aid:
                    folder_name = paper_folder(aid)
                    paper_dir = raw_dir / str(owner) / folder_name
                    if paper_dir.exists() and paper_dir not in counted_dirs:
                        total_bytes += get_dir_size(paper_dir)
                        counted_dirs.add(paper_dir)
        else:
            total_bytes += get_dir_size(raw_dir)

    # Fallback: if calculated total is still 0, check output_root itself
    if total_bytes == 0:
        total_bytes = get_dir_size(output_root)

    return total_bytes


def extract_run_timings(output_root: Path, run_id: str) -> dict[str, Any]:
    """Extract granular task durations: total discovery time, per-paper time, and mean paper time."""
    run_dir = output_root / "runs" / run_id
    if not run_dir.exists() and (output_root / "scope.json").exists():
        run_dir = output_root

    results_file = run_dir / "accepted_results.jsonl"
    if not results_file.exists():
        # Try logs
        results_file = output_root / "logs" / run_id / "events.jsonl"

    discovery_seconds: float = 0.0
    has_discovery: bool = False
    per_paper_seconds: dict[str, float] = {}

    if results_file.exists():
        try:
            with open(results_file, "r", encoding="utf-8") as f:
                for line in f:
                    line_str = line.strip()
                    if not line_str:
                        continue
                    r = json.loads(line_str)
                    step = r.get("step") or r.get("task_type")
                    duration = r.get("duration_seconds")
                    dur_float = float(duration) if isinstance(duration, (int, float)) and duration > 0 else 0.0

                    if step == "discovery" and dur_float > 0:
                        discovery_seconds += dur_float
                        has_discovery = True
                    elif step in ("metadata", "sources", "references") and dur_float > 0:
                        aid = r.get("arxiv_id")
                        if aid:
                            per_paper_seconds[aid] = round(per_paper_seconds.get(aid, 0.0) + dur_float, 4)
        except Exception:
            pass

    paper_durations = list(per_paper_seconds.values())
    mean_paper_sec = round(sum(paper_durations) / len(paper_durations), 4) if paper_durations else None
    min_paper_sec = round(min(paper_durations), 4) if paper_durations else None
    max_paper_sec = round(max(paper_durations), 4) if paper_durations else None

    return {
        "discovery_seconds": round(discovery_seconds, 4) if has_discovery else None,
        "mean_paper_seconds": mean_paper_sec,
        "min_paper_seconds": min_paper_sec,
        "max_paper_seconds": max_paper_sec,
        "per_paper_seconds": per_paper_seconds,
        "total_paper_task_seconds": round(sum(paper_durations), 4) if paper_durations else None,
    }


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

        # Default fallback scope if not found
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
        wall_seconds: float | None = None,
        exit_code: int = 0,
        cache_mode: Literal["cold", "warm", "mixed"] = "cold",
        peak_ram_bytes: int | None = None,
        mean_ram_bytes: int | None = None,
        peak_disk_bytes: int | None = None,
        sampling_interval_seconds: float = 0.1,
        method: str | None = None,
    ) -> Benchmark:
        """Construct a validated Schema 1.2 Benchmark record with granular timings."""
        scope, is_mock = self._load_run_scope(output_root, run_id)
        paper_tasks = scope.get("paper_tasks", [])

        # Measure complete final output size
        final_output_bytes = compute_complete_output_bytes(output_root, run_id, paper_tasks)

        if peak_disk_bytes is None or peak_disk_bytes < final_output_bytes:
            peak_disk_bytes = final_output_bytes if final_output_bytes > 0 else None

        # Extract timing metrics
        timings = extract_run_timings(output_root, run_id)

        # Wall seconds check: do not return 0.0 if not measured
        safe_wall_seconds: float
        if wall_seconds is not None and wall_seconds > 0:
            safe_wall_seconds = round(float(wall_seconds), 4)
        else:
            # If wall_seconds not measured directly, sum discovery + paper tasks if available
            est_total = (timings.get("discovery_seconds") or 0.0) + (timings.get("total_paper_task_seconds") or 0.0)
            safe_wall_seconds = round(est_total, 4)

        if method is None:
            _, method = get_system_ram_used()

        record: Benchmark = {
            "schema_version": SCHEMA_VERSION,
            "is_mock": is_mock,
            "run_id": run_id,
            "scope": scope,
            "environment": get_environment_info(),
            "cache_mode": cache_mode,
            "wall_seconds": safe_wall_seconds,
            "peak_ram_bytes": peak_ram_bytes,
            "peak_disk_bytes": peak_disk_bytes,
            "final_output_bytes": final_output_bytes,
            "method": method,
            "mean_ram_bytes": mean_ram_bytes,
            "sampling_interval_seconds": sampling_interval_seconds,
            "exit_code": exit_code,
        }

        # Extend with granular timings
        record["timing"] = timings  # type: ignore
        return record

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
        """Execute a Python callable while sampling system-wide RAM and disk metrics."""
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
        """Run a command as subprocess while sampling system-wide RAM and disk metrics."""
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
