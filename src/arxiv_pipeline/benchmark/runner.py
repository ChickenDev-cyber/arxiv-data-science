"""Benchmark measurement and execution orchestrator.

Tracks pipeline process resources (RAM, Disk) and granular task timings.
Preserves exit codes (including 2 and 130) and persists benchmarks on Ctrl+C.
Official benchmarks run on Google Colab CPU (Linux).
"""

from __future__ import annotations

import json
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Literal

from ..contracts import Benchmark, Scope, SCHEMA_VERSION
from .profiler import ResourceSampler, get_dir_size, get_environment_info, get_pipeline_process_ram


def compute_complete_output_bytes(
    output_root: Path,
    run_id: str | None = None,
) -> int:
    """Measure the complete byte footprint of all files in output root, never following symlinks.
    
    Includes staging, scratch, raw corpus, discovery, logs, SQLite databases and runs.
    """
    if not output_root.exists() or output_root.is_symlink():
        return 0
    return get_dir_size(output_root)


def extract_run_timings(output_root: Path, run_id: str) -> dict[str, Any]:
    """Extract task durations: discovery time and per-paper processing times.
    
    Prefers logs/<run_id>/events.jsonl (to include retries/failures) over accepted_results.jsonl.
    """
    events_file = output_root / "logs" / run_id / "events.jsonl"
    results_file = output_root / "runs" / run_id / "accepted_results.jsonl"

    target_file = events_file if events_file.exists() else results_file
    if not target_file.exists() and (output_root / "accepted_results.jsonl").exists():
        target_file = output_root / "accepted_results.jsonl"

    discovery_seconds: float = 0.0
    has_discovery: bool = False
    per_paper_seconds: dict[str, float] = {}

    if target_file.exists():
        try:
            with open(target_file, "r", encoding="utf-8") as f:
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

        # Fallback synthetic scope
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
        """Construct a validated Schema 1.2 Benchmark record."""
        scope, is_mock = self._load_run_scope(output_root, run_id)

        # Measure complete final output size (including staging/scratch, ignoring symlinks)
        final_output_bytes = compute_complete_output_bytes(output_root, run_id)

        if peak_disk_bytes is None or peak_disk_bytes < final_output_bytes:
            peak_disk_bytes = final_output_bytes if final_output_bytes > 0 else None

        # Extract timing metrics
        timings = extract_run_timings(output_root, run_id)

        # Wall seconds: must reflect actual measured elapsed invocation time, not sum of durations
        safe_wall_seconds = round(float(wall_seconds), 4) if (wall_seconds is not None and wall_seconds > 0) else 0.0

        if method is None:
            _, method = get_pipeline_process_ram()

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

        record["timing"] = timings  # type: ignore
        return record

    def benchmark_callable(
        self,
        fn: Callable[..., Any],
        *args: Any,
        run_id: str = "callable-bench",
        output_root: Path = Path("."),
        output_dir: Path = Path("benchmarks"),
        cache_mode: Literal["cold", "warm", "mixed"] = "cold",
        interval: float | None = None,
        **kwargs: Any,
    ) -> tuple[Any, Benchmark]:
        """Execute a Python callable while sampling process tree RAM and disk metrics.
        
        Preserves exit codes (e.g. 2, 130) and saves benchmark file on Ctrl+C.
        """
        sampling_interval = interval or self.default_interval
        sampler = ResourceSampler(target_dir=output_root, interval_seconds=sampling_interval)

        start_time = time.perf_counter()
        sampler.start()
        exit_code = 0
        result = None

        try:
            result = fn(*args, **kwargs)
        except KeyboardInterrupt:
            wall_seconds = time.perf_counter() - start_time
            peak_ram, mean_ram, peak_disk, method = sampler.stop()
            exit_code = 130
            bench = self.build_benchmark(
                run_id=run_id,
                output_root=output_root,
                wall_seconds=wall_seconds,
                exit_code=130,
                cache_mode=cache_mode,
                peak_ram_bytes=peak_ram,
                mean_ram_bytes=mean_ram,
                peak_disk_bytes=peak_disk,
                sampling_interval_seconds=sampling_interval,
                method=method,
            )
            self.save_benchmark(bench, output_dir / f"{run_id}.json")
            raise KeyboardInterrupt()
        except SystemExit as se:
            exit_code = se.code if isinstance(se.code, int) else 1
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
            self.save_benchmark(bench, output_dir / f"{run_id}.json")
            raise se
        except Exception as exc:
            exit_code = 1
            wall_seconds = time.perf_counter() - start_time
            peak_ram, mean_ram, peak_disk, method = sampler.stop()
            bench = self.build_benchmark(
                run_id=run_id,
                output_root=output_root,
                wall_seconds=wall_seconds,
                exit_code=1,
                cache_mode=cache_mode,
                peak_ram_bytes=peak_ram,
                mean_ram_bytes=mean_ram,
                peak_disk_bytes=peak_disk,
                sampling_interval_seconds=sampling_interval,
                method=method,
            )
            self.save_benchmark(bench, output_dir / f"{run_id}.json")
            raise exc
        finally:
            if exit_code not in (1, 130) and result is not None:
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
        output_dir: Path = Path("benchmarks"),
        cache_mode: Literal["cold", "warm", "mixed"] = "cold",
        interval: float | None = None,
    ) -> Benchmark:
        """Run an external command while monitoring process tree RAM and disk metrics.
        
        Preserves exit codes (e.g. 2, 130) and saves benchmark file on Ctrl+C.
        """
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
        exit_code = 0

        try:
            exit_code = proc.wait()
        except KeyboardInterrupt:
            wall_seconds = time.perf_counter() - start_time
            peak_ram, mean_ram, peak_disk, method = sampler.stop()
            proc.kill()
            exit_code = 130
            bench = self.build_benchmark(
                run_id=run_id,
                output_root=output_root,
                wall_seconds=wall_seconds,
                exit_code=130,
                cache_mode=cache_mode,
                peak_ram_bytes=peak_ram,
                mean_ram_bytes=mean_ram,
                peak_disk_bytes=peak_disk,
                sampling_interval_seconds=sampling_interval,
                method=method,
            )
            self.save_benchmark(bench, output_dir / f"{run_id}.json")
            raise KeyboardInterrupt()
        finally:
            if exit_code != 130:
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
        self.save_benchmark(bench, output_dir / f"{run_id}.json")
        return bench

    def save_benchmark(self, benchmark_data: Benchmark, output_file: Path) -> Path:
        """Save benchmark JSON data to file."""
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(benchmark_data, f, indent=2, ensure_ascii=False)
        return output_file
