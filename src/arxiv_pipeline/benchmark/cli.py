"""CLI interface for arXiv Pipeline Benchmark Profiler."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .runner import BenchmarkService


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
        help="Optional command string to execute and profile in real-time.",
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
    service = BenchmarkService(default_interval=args.interval)

    try:
        if args.cmd:
            print(f"Benchmarking command: {args.cmd}")
            bench = service.benchmark_command(
                cmd=args.cmd,
                run_id=args.run_id,
                output_root=args.output_root,
                cache_mode=args.cache_mode,
                interval=args.interval,
            )
        else:
            print(f"Recording benchmark summary for existing run: {args.run_id}...")
            bench = service.build_benchmark(
                run_id=args.run_id,
                output_root=args.output_root,
                wall_seconds=None,
                exit_code=0,
                cache_mode=args.cache_mode,
                sampling_interval_seconds=args.interval,
            )

        out_file = args.output_dir / f"{args.run_id}.json"
        service.save_benchmark(bench, out_file)

        wall_sec = bench.get("wall_seconds")
        wall_str = f"{wall_sec:.2f}s" if wall_sec and wall_sec > 0 else "N/A (Not measured)"

        peak_ram = bench.get("peak_ram_bytes")
        peak_str = f"{peak_ram / (1024 * 1024):.2f} MB" if peak_ram and peak_ram > 0 else "N/A (Not measured)"

        mean_ram = bench.get("mean_ram_bytes")
        mean_str = f"{mean_ram / (1024 * 1024):.2f} MB" if mean_ram and mean_ram > 0 else "N/A (Not measured)"

        final_bytes = bench.get("final_output_bytes") or 0
        final_str = f"{final_bytes / 1024:.2f} KB" if final_bytes > 0 else "N/A (Not measured)"

        peak_disk = bench.get("peak_disk_bytes")
        disk_str = f"{peak_disk / 1024:.2f} KB" if peak_disk and peak_disk > 0 else "N/A (Not measured)"

        timings = bench.get("timing") or {}
        disc_str = f"{timings.get('discovery_seconds'):.2f}s" if timings.get('discovery_seconds') else "N/A (Not measured)"
        mean_p_str = f"{timings.get('mean_paper_seconds'):.2f}s" if timings.get('mean_paper_seconds') else "N/A (Not measured)"

        print(f"[SUCCESS] Benchmark report saved to: {out_file}")
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
