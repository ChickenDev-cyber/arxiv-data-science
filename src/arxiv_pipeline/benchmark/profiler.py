"""System environment inspection and background resource usage sampling.

Measures RAM of the pipeline process tree (RSS) including the wrapper and runner processes,
never whole-machine RAM, and never falls back to wrapper Python heap (tracemalloc).
Mean RAM is calculated with time-weighting (trapezoidal integration over elapsed time).
Official benchmarks run on Google Colab CPU (Linux).
Windows and mock data serve local unit tests and development.
"""

from __future__ import annotations

import os
import platform
import threading
import time
from pathlib import Path
from typing import Any, Iterable

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False


def get_environment_info() -> dict[str, str]:
    """Capture runtime system and hardware metadata."""
    info = {
        "os": f"{platform.system()} {platform.release()}",
        "os_version": platform.version(),
        "architecture": platform.machine(),
        "processor": platform.processor() or "Unknown",
        "cpu_count": str(os.cpu_count() or 1),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
    }

    # Total physical system RAM (for reference only, not used as process consumption)
    if HAS_PSUTIL:
        try:
            info["total_ram_bytes"] = str(psutil.virtual_memory().total)
        except Exception:
            pass
    elif Path("/proc/meminfo").exists():
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    parts = line.split()
                    info["total_ram_bytes"] = str(int(parts[1]) * 1024)
                    break
        except Exception:
            pass

    return info


def get_pipeline_process_ram(pids: int | Iterable[int] | None = None) -> tuple[int | None, str]:
    """Get the RSS of the pipeline wrapper process and runner process trees.

    Measures pipeline processes only (wrapper + runner + all descendants);
    does NOT return entire system RAM and does NOT fall back to Python heap (tracemalloc).
    """
    if pids is None:
        root_pids = [os.getpid()]
    elif isinstance(pids, int):
        root_pids = [pids]
    else:
        root_pids = [p for p in pids if isinstance(p, int) and p > 0]

    if not root_pids:
        root_pids = [os.getpid()]

    # 1. psutil: collect unique PIDs across wrapper and all descendants
    if HAS_PSUTIL:
        try:
            seen_pids: set[int] = set()
            total_rss = 0
            for r_pid in root_pids:
                try:
                    proc = psutil.Process(r_pid)
                    if proc.pid not in seen_pids:
                        seen_pids.add(proc.pid)
                        total_rss += proc.memory_info().rss
                    for child in proc.children(recursive=True):
                        if child.pid not in seen_pids:
                            seen_pids.add(child.pid)
                            total_rss += child.memory_info().rss
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            if seen_pids:
                return int(total_rss), "process_tree_rss (psutil)"
        except Exception:
            pass

    # 2. Linux /proc/<pid>/status: VmRSS
    total_proc_rss = 0
    found_any = False
    for r_pid in root_pids:
        status_file = Path(f"/proc/{r_pid}/status")
        if status_file.exists():
            try:
                for line in status_file.read_text().splitlines():
                    if line.startswith("VmRSS:"):
                        parts = line.split()
                        total_proc_rss += int(parts[1]) * 1024
                        found_any = True
                        break
            except Exception:
                pass

    if found_any:
        return total_proc_rss, "process_rss (/proc)"

    return None, "unsupported"


def get_dir_size(path: Path) -> int:
    """Calculate total byte size of all files in directory tree, never following symlinks."""
    if not path.exists() or path.is_symlink():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0

    total = 0
    try:
        for entry in path.rglob("*"):
            # Never follow or count symlinks
            if entry.is_symlink():
                continue
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


class ResourceSampler:
    """Background thread sampling pipeline process tree RAM and disk footprint.

    Tracks pipeline process RSS only (wrapper and runner process tree);
    never measures whole-machine RAM, never falls back to wrapper Python heap,
    ignores symlinks, and calculates time-weighted mean RAM.
    """

    def __init__(
        self,
        target_dir: Path | None = None,
        interval_seconds: float = 0.1,
        process_pid: int | None = None,
        extra_pids: Iterable[int] | None = None,
    ):
        self.target_dir = target_dir
        self.interval_seconds = max(0.01, interval_seconds)

        # Include wrapper process and runner process PID
        root_pids: set[int] = {os.getpid()}
        if process_pid and process_pid > 0:
            root_pids.add(process_pid)
        if extra_pids:
            root_pids.update(p for p in extra_pids if isinstance(p, int) and p > 0)
        self.monitored_pids = sorted(root_pids)

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        # Store tuples of (timestamp, ram_bytes)
        self.ram_samples: list[tuple[float, int]] = []
        self.disk_samples: list[int] = []
        self.peak_ram_bytes: int | None = None
        self.peak_disk_bytes: int | None = None
        self.measurement_method: str = "none"

    def _sample_once(self) -> None:
        now = time.perf_counter()
        ram, method = get_pipeline_process_ram(self.monitored_pids)
        if ram is not None:
            self.ram_samples.append((now, ram))
            self.measurement_method = method
            if self.peak_ram_bytes is None or ram > self.peak_ram_bytes:
                self.peak_ram_bytes = ram

        if self.target_dir and self.target_dir.exists():
            disk = get_dir_size(self.target_dir)
            self.disk_samples.append(disk)
            if self.peak_disk_bytes is None or disk > self.peak_disk_bytes:
                self.peak_disk_bytes = disk

    def _sample_loop(self) -> None:
        while not self._stop_event.is_set():
            self._sample_once()
            self._stop_event.wait(self.interval_seconds)

    def start(self) -> None:
        """Start the background sampler."""
        self.ram_samples.clear()
        self.disk_samples.clear()
        self.peak_ram_bytes = None
        self.peak_disk_bytes = None
        self._stop_event.clear()

        # Initial baseline sample
        self._sample_once()

        self._thread = threading.Thread(target=self._sample_loop, daemon=True)
        self._thread.start()

    def stop(self) -> tuple[int | None, int | None, int | None, str]:
        """Stop sampling and return (peak_ram, time_weighted_mean_ram, peak_disk, method).

        Returns None for unmeasured metrics; never returns 0 as a placeholder.
        Mean RAM is calculated with time-weighting (trapezoidal integration over elapsed time).
        """
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

        # Final sample
        self._sample_once()

        mean_ram: int | None = None
        if self.ram_samples:
            if len(self.ram_samples) == 1:
                mean_ram = self.ram_samples[0][1]
            else:
                total_weighted = 0.0
                total_duration = 0.0
                for i in range(1, len(self.ram_samples)):
                    t_prev, r_prev = self.ram_samples[i - 1]
                    t_curr, r_curr = self.ram_samples[i]
                    dt = t_curr - t_prev
                    if dt > 0:
                        # Trapezoidal rule for time-weighted average
                        avg_ram = (r_prev + r_curr) / 2.0
                        total_weighted += avg_ram * dt
                        total_duration += dt
                if total_duration > 0:
                    mean_ram = int(round(total_weighted / total_duration))
                else:
                    mean_ram = self.ram_samples[0][1]

        return self.peak_ram_bytes, mean_ram, self.peak_disk_bytes, self.measurement_method
