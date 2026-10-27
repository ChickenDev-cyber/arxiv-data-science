"""CLI interface for arXiv Pipeline Statistics Module."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .service import StatisticsService


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate Schema 1.2 Statistics from arXiv pipeline runs."
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default="baseline-001",
        help="ID of the pipeline run (default: baseline-001).",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("."),
        help="Root directory of pipeline runs (default: .).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("statistics"),
        help="Directory to store generated statistics JSON (default: statistics).",
    )
    parser.add_argument(
        "--aggregate-runs",
        nargs="+",
        type=str,
        help="List of run_ids to aggregate together into a team summary.",
    )
    parser.add_argument(
        "--aggregate-id",
        type=str,
        default="team-aggregated",
        help="Run ID for the aggregated result (default: team-aggregated).",
    )
    parser.add_argument(
        "--no-validation",
        action="store_true",
        help="Do not enforce validation.valid check from validation.json.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    service = StatisticsService(check_validation=not args.no_validation)

    try:
        if args.aggregate_runs:
            print(f"Aggregating statistics across {len(args.aggregate_runs)} runs...")
            stats_list = []
            for r_id in args.aggregate_runs:
                st = service.build_statistics(args.output_root, r_id)
                stats_list.append(st)

            aggregated_stats = service.aggregate_statistics(stats_list, aggregated_run_id=args.aggregate_id)
            out_file = args.output_dir / f"{args.aggregate_id}.json"
            service.save_statistics(aggregated_stats, out_file)
            print(f"[SUCCESS] Successfully created team aggregated statistics at: {out_file}")
        else:
            print(f"Generating statistics for run: {args.run_id}...")
            stats = service.build_statistics(args.output_root, args.run_id)
            out_file = args.output_dir / f"{args.run_id}.json"
            service.save_statistics(stats, out_file)
            print(f"[SUCCESS] Successfully generated Schema 1.2 statistics at: {out_file}")
            print(
                f"   - Papers discovered: {stats['summary']['papers_discovered']}\n"
                f"   - Metadata complete: {stats['summary']['metadata_complete_papers']}\n"
                f"   - Papers complete: {stats['summary']['papers_complete']} ({stats['summary']['paper_success_rate'] or 0:.1%})\n"
                f"   - Source success rate: {stats['summary']['source_success_rate'] or 0:.1%}\n"
                f"   - Ref complete rate: {stats['summary']['reference_metadata_success_rate'] or 0:.1%}"
            )
        return 0

    except Exception as exc:
        print(f"[ERROR] Error generating statistics: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
