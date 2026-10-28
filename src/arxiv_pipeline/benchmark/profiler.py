"""System environment inspection and background resource usage sampling.

Benchmark chính thức chạy trên Google Colab CPU (Linux).
Windows và dữ liệu giả lập (mock) dùng để kiểm thử cục bộ.
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
    
    # Total system RAM
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


def get_system_ram_used() -> tuple[int | None, str]:
    """Get system-wide used RAM in bytes.
    
    Returns (used_bytes, method_name). Does NOT fall back to Python heap allocations.
    """
    # 1. psutil (works cross-platform: Windows, Linux, macOS)
    if HAS_PSUTIL:
        try:
            vm = psutil.virtual_memory()
            return int(vm.used), "system_ram_used (psutil)"
        except Exception:
            pass

    # 2. Linux /proc/meminfo (Google Colab CPU / Linux servers)
    meminfo_path = Path("/proc/meminfo")
    if meminfo_path.exists():
        try:
            data: dict[str, int] = {}
            for line in meminfo_path.read_text().splitlines():
                parts = line.split(":")
                if len(parts) == 2:
                    k, v = parts[0].strip(), parts[1].strip()
                    val = int(v.split()[0]) * 1024  # kB to bytes
                    data[k] = val
            if "MemTotal" in data and "MemAvailable" in data:
                return data["MemTotal"] - data["MemAvailable"], "system_ram_used (/proc/meminfo)"
            if "MemTotal" in data and "MemFree" in data:
                buffers = data.get("Buffers", 0)
                cached = data.get("Cached", 0)
                return data["MemTotal"] - data["MemFree"] - buffers - cached, "system_ram_used (/proc/meminfo)"
        except Exception:
            pass

    # No valid system RAM measurement available; do not fake it with tracemalloc
    return None, "unsupported"


def get_dir_size(path: Path) -> int:
    """Calculate total byte size of all files in a directory tree, ignoring broken symlinks."""
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
    """Background thread that periodically samples system RAM and disk footprint.
    
    Reflects whole-system / OS-level metrics and never falls back to wrapper Python heap.
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
        ram, method = get_system_ram_used()
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
        
        Returns None for metrics that could not be measured (never fakes as 0).
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
