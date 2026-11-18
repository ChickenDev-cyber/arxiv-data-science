"""Command-line interface entrypoint for hierarchical parsing and benchmark reporting."""

import argparse
import json
import logging
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional

from arxiv_pipeline.parsing.runner import run_parsing_pipeline
from arxiv_pipeline.parsing.storage import ParsingDatabase

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for parsing CLI."""
    parser = argparse.ArgumentParser(
        prog="arxiv-parsing",
        description="Run hierarchical parsing pipeline, deduplication, and relational storage.",
    )
    parser.add_argument(
        "--corpus",
        required=True,
        type=str,
        help="Path to input corpus directory containing papers.",
    )
    parser.add_argument(
        "--db",
        default="data/m2_parsing.sqlite",
        type=str,
        help="Path to output SQLite database (default: data/m2_parsing.sqlite).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional int limit on number of papers to parse.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Transaction commit batch size (default: 100).",
    )
    parser.add_argument(
        "--benchmark",
        type=str,
        default=None,
        help="Optional path to write benchmark JSON report.",
    )
    parser.add_argument(
        "--parquet-dir",
        type=str,
        default=None,
        help="Optional directory to export Parquet tables (if pyarrow available).",
    )
    parser.add_argument(
        "--single-parquet",
        action="store_true",
        help="Export to 3 single Parquet files (elements.parquet, hierarchy.parquet, bibtex.parquet) instead of sharded directories.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose debug logging.",
    )
    parser.add_argument("--no-python-heap-tracing", action="store_true",
                        help="Disable tracemalloc overhead when measuring process RSS externally.")
    parser.add_argument('--resume', action='store_true',
                        help='Enable verified per-paper checkpoints (rule backend only).')
    parser.add_argument('--backup-db', type=Path, help='Write a consistent checkpoint snapshot; choose persistent storage.')
    parser.add_argument('--backup-every', type=int, default=100, help='Backup every N newly processed papers, and on graceful exit.')
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Execute parsing CLI workflow."""
    parser = build_parser()
    args = parser.parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    summary: Dict[str, Any] = run_parsing_pipeline(
        corpus_dir=args.corpus,
        db_path=args.db,
        limit=args.limit,
        batch_size=args.batch_size,
        track_python_memory=not args.no_python_heap_tracing,
        resume=args.resume, backup_path=args.backup_db, backup_every=args.backup_every,
    )

    if args.parquet_dir:
        try:
            with ParsingDatabase(args.db) as db:
                if getattr(args, "single_parquet", False):
                    export_counts = db.export_to_parquet(args.parquet_dir, single_file=True)
                else:
                    export_counts = db.export_to_parquet(args.parquet_dir)
                summary["parquet_export"] = export_counts
            pq_dir = Path(args.parquet_dir)
            pq_bytes = sum(f.stat().st_size for f in pq_dir.rglob("*.parquet")) if pq_dir.exists() else 0
            summary["parquet_size_mb"] = pq_bytes / (1024 * 1024)
        except RuntimeError as e:
            logger.warning("Parquet export skipped: %s", e)
            summary["parquet_export"] = None
            summary["parquet_export_note"] = str(e)
        except Exception as e:
            logger.error("Failed Parquet export: %s", e)
            summary["parquet_export"] = None
            summary["parquet_export_error"] = str(e)

    if args.benchmark:
        bench_path = Path(args.benchmark)
        bench_path.parent.mkdir(parents=True, exist_ok=True)
        with open(bench_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)

    # Clean JSON summary to stdout
    sys.stdout.write(json.dumps(summary, indent=2) + "\n")
    sys.stdout.flush()

    return 0


if __name__ == "__main__":
    sys.exit(main())
