"""CLI interface for arXiv Pipeline Benchmark Profiler."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .runner import BenchmarkService


def format_seconds(sec: float | None) -> str:
    """Format duration in seconds. None returns 'N/A (Not measured)'."""
    return f"{sec:.2f}s" if sec is not None else "N/A (Not measured)"


def format_megabytes(b: int | float | None) -> str:
    """Format bytes into MB. None returns 'N/A (Not measured)'."""
    return f"{b / (1024 * 1024):.2f} MB" if b is not None else "N/A (Not measured)"


def format_kilobytes(b: int | float | None) -> str:
    """Format bytes into KB. None returns 'N/A (Not measured)'."""
    return f"{b / 1024:.2f} KB" if b is not None else "N/A (Not measured)"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Profile and record Schema 1.2 Benchmark metrics for pipeline runs."
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default="baseline-001",
        help="Pipeline run ID (default: baseline-001).",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("."),
        help="Pipeline root directory (default: .).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("benchmarks"),
        help="Directory where benchmark JSON will be saved (default: benchmarks).",
    )
    parser.add_argument(
        "--cmd",
        type=str,
        help="Command string to execute and profile in real-time (required for measured benchmark).",
    )
    parser.add_argument(
        "--cache-mode",
        choices=["cold", "warm", "mixed"],
        default="cold",
        help="Cache mode of this execution (default: cold).",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.1,
        help="Resource sampling interval in seconds (default: 0.1).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.cmd or not args.cmd.strip():
        print("[ERROR] Benchmark profiling requires --cmd specifying a command to execute.", file=sys.stderr)
        return 1

    service = BenchmarkService(default_interval=args.interval)

    try:
        timing_file = args.output_dir / f"{args.run_id}-timing.json"
        print(f"Benchmarking command: {args.cmd}")
        bench = service.benchmark_command(
            cmd=args.cmd,
            run_id=args.run_id,
            output_root=args.output_root,
            output_dir=args.output_dir,
            cache_mode=args.cache_mode,
            interval=args.interval,
        )

        out_file = args.output_dir / f"{args.run_id}.json"

        # Load sidecar timing file to print real timings
        timings: dict[str, Any] = {}
        if timing_file.exists():
            try:
                timings = json.loads(timing_file.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                print(f"[WARNING] Cannot read timing report {timing_file}: {exc}", file=sys.stderr)

        wall_sec = bench.get("wall_seconds")
        wall_str = format_seconds(wall_sec)

        peak_ram = bench.get("peak_ram_bytes")
        peak_str = format_megabytes(peak_ram)

        mean_ram = bench.get("mean_ram_bytes")
        mean_str = format_megabytes(mean_ram)

        final_bytes = bench.get("final_output_bytes")
        final_str = format_kilobytes(final_bytes)

        peak_disk = bench.get("peak_disk_bytes")
        disk_str = format_kilobytes(peak_disk)

        disc_sec = timings.get("discovery_seconds")
        disc_str = format_seconds(disc_sec)
        mean_p_sec = timings.get("mean_paper_seconds")
        mean_p_str = format_seconds(mean_p_sec)

        print(f"[SUCCESS] Benchmark report saved to: {out_file}")
        if timing_file.exists():
            print(f"[SUCCESS] Timing sidecar report saved to: {timing_file}")
        print(f"   - Wall Time: {wall_str}")
        print(f"   - Discovery Time: {disc_str}")
        print(f"   - Mean Paper Time: {mean_p_str}")
        print(f"   - Peak RAM: {peak_str}")
        print(f"   - Mean RAM: {mean_str}")
        print(f"   - Final Output Size: {final_str}")
        print(f"   - Peak Disk Size: {disk_str}")
        print(f"   - Method: {bench.get('method') or 'N/A'}")
        print(f"   - Exit Code: {bench.get('exit_code')}")
        return bench.get("exit_code", 0)

    except Exception as exc:
        print(f"[ERROR] Error running benchmark: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
