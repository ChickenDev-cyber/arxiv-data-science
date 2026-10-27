"""System environment inspection and background resource usage sampling."""

from __future__ import annotations

import os
import platform
import threading
import time
import tracemalloc
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
    if HAS_PSUTIL:
        try:
            mem = psutil.virtual_memory()
            info["total_ram_bytes"] = str(mem.total)
        except Exception:
            pass
    return info


def get_dir_size(path: Path) -> int:
    """Calculate total byte size of all files in directory tree, ignoring broken symlinks."""
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0

    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


class ResourceSampler:
    """Background thread that periodically samples memory and disk footprint."""

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
        self.peak_ram_bytes: int = 0
        self.peak_disk_bytes: int = 0
        self.measurement_method: str = "tracemalloc"

    def _get_current_ram(self) -> int:
        if HAS_PSUTIL:
            try:
                proc = psutil.Process(self.process_pid)
                # RSS of the current process + children
                rss = proc.memory_info().rss
                for child in proc.children(recursive=True):
                    try:
                        rss += child.memory_info().rss
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                self.measurement_method = "psutil_rss"
                return rss
            except Exception:
                pass

        # Fallback to tracemalloc
        self.measurement_method = "tracemalloc"
        current, peak = tracemalloc.get_traced_memory()
        return peak or current

    def _sample_loop(self) -> None:
        while not self._stop_event.is_set():
            ram = self._get_current_ram()
            self.ram_samples.append(ram)
            if ram > self.peak_ram_bytes:
                self.peak_ram_bytes = ram

            if self.target_dir and self.target_dir.exists():
                disk = get_dir_size(self.target_dir)
                self.disk_samples.append(disk)
                if disk > self.peak_disk_bytes:
                    self.peak_disk_bytes = disk

            self._stop_event.wait(self.interval_seconds)

    def start(self) -> None:
        """Start the background sampler."""
        if not HAS_PSUTIL:
            if not tracemalloc.is_tracing():
                tracemalloc.start()

        self.ram_samples.clear()
        self.disk_samples.clear()
        self.peak_ram_bytes = 0
        self.peak_disk_bytes = 0
        self._stop_event.clear()

        # Initial baseline sample
        ram = self._get_current_ram()
        self.ram_samples.append(ram)
        self.peak_ram_bytes = ram
        if self.target_dir:
            disk = get_dir_size(self.target_dir)
            self.disk_samples.append(disk)
            self.peak_disk_bytes = disk

        self._thread = threading.Thread(target=self._sample_loop, daemon=True)
        self._thread.start()

    def stop(self) -> tuple[int, int | None, int, str]:
        """Stop sampling and return (peak_ram, mean_ram, peak_disk, method)."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

        # Final sample
        ram = self._get_current_ram()
        self.ram_samples.append(ram)
        if ram > self.peak_ram_bytes:
            self.peak_ram_bytes = ram

        if self.target_dir and self.target_dir.exists():
            disk = get_dir_size(self.target_dir)
            if disk > self.peak_disk_bytes:
                self.peak_disk_bytes = disk

        mean_ram = (
            int(sum(self.ram_samples) / len(self.ram_samples))
            if self.ram_samples
            else self.peak_ram_bytes
        )

        return self.peak_ram_bytes, mean_ram, self.peak_disk_bytes, self.measurement_method
