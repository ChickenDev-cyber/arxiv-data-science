"""CLI interface for arXiv Pipeline Statistics Module."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .service import StatisticsService


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate Schema 1.2 Statistics from arXiv pipeline runs or verified collections."
    )
    parser.add_argument(
        "--collection",
        type=Path,
        default=None,
        help="Path to verified collection directory (containing collection.json).",
    )
    parser.add_argument(
        "--output-root",
        "--output-roots",
        dest="output_roots",
        nargs="+",
        type=Path,
        default=None,
        help="One or more output roots for single or multiple pipeline runs.",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="ID of the pipeline run (default: baseline-001 or discovered from output root).",
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
        help="DEPRECATED: Use --collection <path> to aggregate verified collections.",
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


def _discover_run_id(output_root: Path) -> str:
    db_path = output_root / "state.sqlite"
    if db_path.exists():
        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            row = conn.execute("SELECT run_id FROM runs LIMIT 1").fetchone()
            conn.close()
            if row and row[0]:
                return str(row[0])
        except Exception:
            pass
    # Check runs/ subdirectory
    runs_dir = output_root / "runs"
    if runs_dir.is_dir():
        subdirs = [d.name for d in runs_dir.iterdir() if d.is_dir()]
        if len(subdirs) == 1:
            return subdirs[0]
    return output_root.name


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.aggregate_runs is not None:
        print(
            "[ERROR] --aggregate-runs is deprecated; use --collection <path> to aggregate verified collections.",
            file=sys.stderr,
        )
        return 1

    service = StatisticsService(check_validation=not args.no_validation)

    try:
        # Case 1: Aggregate verified collection directory
        if args.collection:
            print(f"Aggregating verified collection from: {args.collection}...")
            from ..collection.reader import CollectionDirectoryReader
            from ..collection.inspector import RunInspector
            reader = CollectionDirectoryReader(service.run_reader or RunInspector())
            with reader.open(args.collection) as v_col:
                col_stats = service.aggregate_statistics(v_col)
                cid = col_stats["collection_id"]
                out_file = args.output_dir / f"{cid}.json"
                service.save_statistics(col_stats, out_file)
                print(f"[SUCCESS] Successfully generated CollectionStatistics at: {out_file}")
                print(
                    f"   - Collection ID: {cid}\n"
                    f"   - Members: {len(col_stats['sources'])}\n"
                    f"   - Papers discovered: {col_stats['summary']['papers_discovered']}\n"
                    f"   - Papers complete: {col_stats['summary']['papers_complete']} ({col_stats['summary']['paper_success_rate'] or 0:.1%})"
                )
            return 0

        # Case 2: Multiple individual output roots
        if args.output_roots and len(args.output_roots) > 1:
            print(f"Generating statistics for {len(args.output_roots)} distinct output roots...")
            runs = [(root, args.run_id or _discover_run_id(root)) for root in args.output_roots]
            seen_ids = set()
            for _, run_id in runs:
                if run_id in seen_ids:
                    raise ValueError(
                        f"Duplicate run ID {run_id!r}: multiple roots would write "
                        f"{args.output_dir / (run_id + '.json')}. "
                        "Use separate output directories or aggregate a verified collection."
                    )
                seen_ids.add(run_id)
            stats_list = []
            for root, r_id in runs:
                st = service.build_statistics(root, r_id)
                stats_list.append(st)
                out_file = args.output_dir / f"{r_id}.json"
                service.save_statistics(st, out_file)
                print(f"[SUCCESS] Generated run statistics for {r_id} at: {out_file}")

            if args.aggregate_id:
                print(
                    "[INFO] To aggregate multiple runs into CollectionStatistics, "
                    "use --collection <path> with an assembled collection."
                )
            return 0

        # Case 3: Single output root
        target_root = args.output_roots[0] if args.output_roots else Path(".")
        r_id = args.run_id or _discover_run_id(target_root)
        if not r_id or r_id == ".":
            r_id = "baseline-001"

        print(f"Generating statistics for run: {r_id} at {target_root}...")
        stats = service.build_statistics(target_root, r_id)
        out_file = args.output_dir / f"{r_id}.json"
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
