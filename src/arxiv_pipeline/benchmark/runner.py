"""Benchmark measurement and execution orchestrator.

Tracks pipeline process resources (RAM of wrapper + runner tree, Disk) and real wall time.
Preserves exit codes (including 2 and 130) and persists benchmarks even on Ctrl+C.
Strictly conforms to Schema 1.2 Benchmark without extra timing fields.
Saves invocation timing breakdown (elapsed per-paper time, discovery) in sidecar files.
Official benchmarks run on Google Colab CPU (Linux).
"""

from __future__ import annotations

import json
import os
import platform
import shlex
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

from ..contracts import Benchmark, Scope, SCHEMA_VERSION
from ..runner.models import RunOutcome
from .profiler import ResourceSampler, get_dir_size, get_environment_info, get_pipeline_process_ram


def _terminate_process_group(
    proc: subprocess.Popen, group: int, timeout: float, interrupt: bool,
) -> None:
    """Stop an isolated POSIX group, even if its original leader has exited."""
    if group == os.getpgrp():
        raise ValueError("Refusing to terminate the benchmark's own process group")
    members = []
    if HAS_PSUTIL:
        try:
            # A descendant can start another session and leave our group.
            members.extend(psutil.Process(proc.pid).children(recursive=True))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        for member in psutil.process_iter():
            try:
                if os.getpgid(member.pid) == group:
                    members.append(member)
            except (ProcessLookupError, PermissionError, psutil.NoSuchProcess):
                continue
        members = list({member.pid: member for member in members}.values())
    try:
        os.killpg(group, signal.SIGINT if interrupt else signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        # Give the runner time to publish a paused checkpoint on Ctrl+C.
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        pass
    try:
        # The saved group ID still addresses descendants after the leader dies.
        os.killpg(group, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait(timeout=1.0)
    if members:
        for member in members:
            try:
                member.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        _, alive = psutil.wait_procs(members, timeout=1.0)
        for member in alive:
            try:
                member.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        _, alive = psutil.wait_procs(alive, timeout=1.0)
        for member in alive:
            try:
                if member.status() not in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD):
                    raise TimeoutError(f"Benchmark descendant {member.pid} did not stop")
            except psutil.NoSuchProcess:
                pass


def terminate_process_tree(
    proc: subprocess.Popen, timeout: float = 5.0, *,
    process_group: int | None = None, interrupt: bool = False,
) -> None:
    """Terminate and wait for the entire process tree (children + grandchildren + parent)."""
    if process_group is not None and os.name == "posix":
        _terminate_process_group(proc, process_group, timeout, interrupt)
        return
    pid = proc.pid
    if HAS_PSUTIL:
        try:
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)
            for child in children:
                try:
                    child.terminate()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            try:
                parent.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

            _, alive = psutil.wait_procs(children + [parent], timeout=timeout)
            for p in alive:
                try:
                    p.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            psutil.wait_procs(alive, timeout=1.0)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        except Exception:
            pass
    else:
        if platform.system() == "Windows":
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=timeout,
                )
            except Exception:
                pass
        else:
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
                time.sleep(0.1)
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except Exception:
                pass

    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=1.0)
    except Exception:
        pass


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


def parse_iso_timestamp(ts: str | None) -> datetime | None:
    if not ts or not isinstance(ts, str):
        return None
    try:
        clean = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(clean)
    except Exception:
        return None


def extract_run_timings(
    output_root: Path,
    run_id: str,
    since_timestamp: str | None = None,
) -> dict[str, Any]:
    """Extract task timings: discovery elapsed time and per-paper elapsed times.

    Measures each paper's duration by elapsed time (max(finish) - min(start)),
    NEVER simply summing individual module durations.
    Prefers logs/<run_id>/events.jsonl (to include retries/failures) over accepted_results.jsonl.
    If since_timestamp is provided, only considers attempts from the current invocation (ignores old resume data).
    """
    events_file = output_root / "logs" / run_id / "events.jsonl"
    results_file = output_root / "runs" / run_id / "accepted_results.jsonl"

    target_file = events_file if events_file.exists() else results_file
    if not target_file.exists() and (output_root / "accepted_results.jsonl").exists():
        target_file = output_root / "accepted_results.jsonl"

    discovery_starts: list[datetime] = []
    discovery_finishes: list[datetime] = []
    discovery_durations: list[float] = []

    paper_timestamps: dict[str, dict[str, list[Any]]] = {}

    if target_file.exists():
        try:
            with open(target_file, "r", encoding="utf-8") as f:
                for line in f:
                    line_str = line.strip()
                    if not line_str:
                        continue
                    r = json.loads(line_str)

                    # Filter out tasks from prior invocations when resuming
                    finished_str = r.get("finished_at")
                    started_str = r.get("started_at")
                    if since_timestamp:
                        check_ts = finished_str or started_str
                        if check_ts and check_ts < since_timestamp:
                            continue

                    step = r.get("step") or r.get("task_type")
                    duration = r.get("duration_seconds")
                    dur_float = float(duration) if isinstance(duration, (int, float)) and duration > 0 else 0.0

                    t_start = parse_iso_timestamp(started_str)
                    t_finish = parse_iso_timestamp(finished_str)

                    if step == "discovery":
                        if t_start:
                            discovery_starts.append(t_start)
                        if t_finish:
                            discovery_finishes.append(t_finish)
                        if dur_float > 0:
                            discovery_durations.append(dur_float)
                    elif step in ("metadata", "sources", "references"):
                        aid = r.get("arxiv_id")
                        if aid:
                            p_info = paper_timestamps.setdefault(aid, {"starts": [], "finishes": [], "durations": []})
                            if t_start:
                                p_info["starts"].append(t_start)
                            if t_finish:
                                p_info["finishes"].append(t_finish)
                            if dur_float > 0:
                                p_info["durations"].append(dur_float)
        except Exception:
            pass

    # 1. Discovery elapsed time
    discovery_seconds: float | None = None
    if discovery_starts and discovery_finishes:
        disc_elapsed = (max(discovery_finishes) - min(discovery_starts)).total_seconds()
        discovery_seconds = round(max(0.0, disc_elapsed), 4)
    elif discovery_durations:
        discovery_seconds = round(sum(discovery_durations), 4)

    # 2. Per-paper elapsed time (elapsed = max(finish) - min(start))
    per_paper_seconds: dict[str, float] = {}
    for aid, p_info in paper_timestamps.items():
        if p_info["starts"] and p_info["finishes"]:
            elapsed = (max(p_info["finishes"]) - min(p_info["starts"])).total_seconds()
            per_paper_seconds[aid] = round(max(0.0001, elapsed), 4)
        elif p_info["durations"]:
            per_paper_seconds[aid] = round(sum(p_info["durations"]), 4)

    paper_durations = list(per_paper_seconds.values())
    mean_paper_sec = round(sum(paper_durations) / len(paper_durations), 4) if paper_durations else None
    min_paper_sec = round(min(paper_durations), 4) if paper_durations else None
    max_paper_sec = round(max(paper_durations), 4) if paper_durations else None
    total_paper_elapsed = round(sum(paper_durations), 4) if paper_durations else None

    return {
        "discovery_seconds": discovery_seconds,
        "mean_paper_seconds": mean_paper_sec,
        "min_paper_seconds": min_paper_sec,
        "max_paper_seconds": max_paper_sec,
        "per_paper_seconds": per_paper_seconds,
        "total_paper_elapsed_seconds": total_paper_elapsed,
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
        sampling_interval_seconds: float | None = None,
        method: str | None = None,
    ) -> Benchmark:
        """Construct a validated Benchmark record conforming strictly to Schema 1.2."""
        raw_scope, is_mock = self._load_run_scope(output_root, run_id)
        final_disk = compute_complete_output_bytes(output_root, run_id)
        actual_peak_disk = max(peak_disk_bytes or 0, final_disk)

        record: Benchmark = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "is_mock": is_mock,
            "cache_mode": cache_mode,
            "scope": raw_scope,
            "wall_seconds": round(wall_seconds, 4) if wall_seconds is not None else None,
            "peak_ram_bytes": peak_ram_bytes,
            "mean_ram_bytes": mean_ram_bytes,
            "peak_disk_bytes": actual_peak_disk,
            "final_output_bytes": final_disk,
            "exit_code": exit_code,
            "environment": get_environment_info(),
            "sampling_interval_seconds": sampling_interval_seconds or self.default_interval,
            "method": method or "process_tree_rss (psutil)",
        }
        return record

    def benchmark_callable(
        self,
        target_fn: Callable[[], Any],
        run_id: str,
        output_root: Path = Path("."),
        output_dir: Path = Path("benchmarks"),
        cache_mode: Literal["cold", "warm", "mixed"] = "cold",
        interval: float | None = None,
    ) -> tuple[Any, Benchmark]:
        """Execute a Python callable while sampling its process tree RAM and disk usage."""
        sampling_interval = interval or self.default_interval
        start_time = time.perf_counter()
        start_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        sampler = ResourceSampler(
            target_dir=output_root,
            interval_seconds=sampling_interval,
            process_pid=os.getpid(),
        )
        sampler.start()

        result = None
        exit_code = 0
        try:
            result = target_fn()
            if isinstance(result, RunOutcome):
                exit_code = result.exit_code
            elif isinstance(result, int):
                exit_code = result
            elif result is None:
                exit_code = 0
        except KeyboardInterrupt:
            exit_code = 130
            raise
        except SystemExit as se:
            exit_code = se.code if isinstance(se.code, int) else 1
            raise
        except Exception:
            exit_code = 1
            raise
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
            self.save_benchmark(bench, output_dir / f"{run_id}.json")

            # Save invocation timing sidecar file
            timings = extract_run_timings(output_root, run_id, since_timestamp=start_iso)
            timings_payload = {
                "run_id": run_id,
                "wall_seconds": wall_seconds,
                "discovery_seconds": timings.get("discovery_seconds"),
                "mean_paper_seconds": timings.get("mean_paper_seconds"),
                "min_paper_seconds": timings.get("min_paper_seconds"),
                "max_paper_seconds": timings.get("max_paper_seconds"),
                "total_paper_seconds": timings.get("total_paper_elapsed_seconds"),
                "per_paper_seconds": timings.get("per_paper_seconds", {}),
            }
            self.save_timing(timings_payload, output_dir / f"{run_id}-timing.json")

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
        """Run an external command while monitoring process tree RAM and disk.

        Stops and waits for the entire process tree on Ctrl+C or completion.
        Preserves exit codes (e.g. 2, 130) and saves benchmark file even on Ctrl+C.
        """
        sampling_interval = interval or self.default_interval
        cmd_args = shlex.split(cmd) if isinstance(cmd, str) else cmd

        start_time = time.perf_counter()
        start_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        proc = subprocess.Popen(cmd_args, start_new_session=(os.name == "posix"))
        process_group = proc.pid if os.name == "posix" else None
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
            exit_code = 130
            raise
        except SystemExit as se:
            exit_code = se.code if isinstance(se.code, int) else 1
            raise
        except Exception:
            exit_code = 1
            raise
        finally:
            terminate_process_tree(
                proc, process_group=process_group, interrupt=(exit_code == 130),
            )
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

            # Save invocation timing sidecar file
            timings = extract_run_timings(output_root, run_id, since_timestamp=start_iso)
            timings_payload = {
                "run_id": run_id,
                "wall_seconds": wall_seconds,
                "discovery_seconds": timings.get("discovery_seconds"),
                "mean_paper_seconds": timings.get("mean_paper_seconds"),
                "min_paper_seconds": timings.get("min_paper_seconds"),
                "max_paper_seconds": timings.get("max_paper_seconds"),
                "total_paper_seconds": timings.get("total_paper_elapsed_seconds"),
                "per_paper_seconds": timings.get("per_paper_seconds", {}),
            }
            self.save_timing(timings_payload, output_dir / f"{run_id}-timing.json")

        return bench

    def save_benchmark(self, benchmark_data: Benchmark, output_file: Path) -> Path:
        """Save benchmark JSON data to file."""
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(benchmark_data, f, indent=2, ensure_ascii=False)
        return output_file

    def save_timing(self, timing_data: dict, output_file: Path) -> Path:
        """Save invocation timing sidecar JSON data to file."""
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(timing_data, f, indent=2, ensure_ascii=False)
        return output_file
