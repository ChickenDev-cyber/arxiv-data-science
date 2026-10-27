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
                wall_seconds=0.0,
                exit_code=0,
                cache_mode=args.cache_mode,
                sampling_interval_seconds=args.interval,
            )

        out_file = args.output_dir / f"{args.run_id}.json"
        service.save_benchmark(bench, out_file)
        
        peak_mb = (bench['peak_ram_bytes'] or 0) / (1024 * 1024)
        mean_mb = (bench['mean_ram_bytes'] or 0) / (1024 * 1024)
        final_kb = bench['final_output_bytes'] / 1024

        print(f"[SUCCESS] Benchmark report saved to: {out_file}")
        print(f"   - Wall Time: {bench['wall_seconds']}s")
        print(f"   - Peak RAM: {peak_mb:.2f} MB")
        print(f"   - Mean RAM: {mean_mb:.2f} MB")
        print(f"   - Final Output Size: {final_kb:.2f} KB")
        print(f"   - Method: {bench['method']}")
        print(f"   - Exit Code: {bench['exit_code']}")
        return bench["exit_code"]

    except Exception as exc:
        print(f"[ERROR] Error running benchmark: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
