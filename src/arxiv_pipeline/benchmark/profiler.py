"""System environment inspection and background resource usage sampling.

Measures RAM of the pipeline process tree (RSS), never whole-machine RAM,
and never falls back to wrapper Python heap (tracemalloc).
Official benchmarks run on Google Colab CPU (Linux).
Windows and mock data serve local unit tests and development.
"""

from __future__ import annotations

import os
import platform
import threading
import time
from pathlib import Path
from typing import Any

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


def get_pipeline_process_ram(pid: int | None = None) -> tuple[int | None, str]:
    """Get the RSS of the pipeline process and its child processes.
    
    Measures pipeline processes only; does NOT return entire system RAM
    and does NOT fall back to Python heap allocations (tracemalloc).
    """
    target_pid = pid or os.getpid()

    # 1. psutil: process RSS + all recursive child processes
    if HAS_PSUTIL:
        try:
            proc = psutil.Process(target_pid)
            total_rss = proc.memory_info().rss
            for child in proc.children(recursive=True):
                try:
                    total_rss += child.memory_info().rss
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            return int(total_rss), "process_tree_rss (psutil)"
        except Exception:
            pass

    # 2. Linux /proc/<pid>/status: VmRSS
    status_file = Path(f"/proc/{target_pid}/status")
    if status_file.exists():
        try:
            for line in status_file.read_text().splitlines():
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    return int(parts[1]) * 1024, "process_rss (/proc)"
        except Exception:
            pass

    # Never fall back to tracemalloc or machine total RAM
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
    
    Tracks pipeline process RSS only; never measures whole-machine RAM,
    never falls back to wrapper Python heap, and ignores symlinks.
    """

    def __init__(
        self,
        target_dir: Path | None = None,
        interval_seconds: float = 0.1,
        process_pid: int | None = None,
    ):
        self.target_dir = target_dir
        self.interval_seconds = max(0.01, interval_seconds)
        self.process_pid = process_pid or os.getpid()

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        self.ram_samples: list[int] = []
        self.disk_samples: list[int] = []
        self.peak_ram_bytes: int | None = None
        self.peak_disk_bytes: int | None = None
        self.measurement_method: str = "none"

    def _sample_once(self) -> None:
        ram, method = get_pipeline_process_ram(self.process_pid)
        if ram is not None:
            self.ram_samples.append(ram)
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
        """Stop sampling and return (peak_ram, mean_ram, peak_disk, method).
        
        Returns None for unmeasured metrics; never returns 0 as a placeholder.
        """
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

        # Final sample
        self._sample_once()

        mean_ram: int | None = None
        if self.ram_samples:
            mean_ram = int(sum(self.ram_samples) / len(self.ram_samples))

        return self.peak_ram_bytes, mean_ram, self.peak_disk_bytes, self.measurement_method
