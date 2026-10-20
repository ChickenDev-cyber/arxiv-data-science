"""Run a T1 discovery preview and write its Result as JSON."""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from ..config import ASSIGNED_RANGES, OWNERS
from .api import ApiClient
from .service import DiscoveryModule, candidate_count, validate_context


def _write_preview(path, result):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".preview-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True, choices=sorted(OWNERS))
    parser.add_argument("--run-id", default="t1-preview")
    parser.add_argument("--output-root", type=Path, default=Path("/tmp/arxiv-t1"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--max-candidates", type=int, help="test at most N candidate IDs (default: 10)")
    mode.add_argument("--full-range", action="store_true", help="explicitly opt into the full assigned range")
    args = parser.parse_args(argv)
    limit = None if args.full_range else args.max_candidates if args.max_candidates is not None else 10
    config = next(dict(c) for c in ASSIGNED_RANGES if c["owner"] == args.owner)
    context = dict(run_id=args.run_id, is_mock=False, output_root=args.output_root)
    try:
        validate_context(context)
        client = ApiClient()
        module = DiscoveryModule(client, max_candidates=limit)
        count = candidate_count(config)
        selected = count if limit is None else min(count, limit)
        print(f"{args.owner}: {selected}/{count} candidate IDs; "
              f"at least {(selected + 99) // 100} batch request(s), excluding retries/rechecks.", file=sys.stderr)
        result = module.discover(config, context)
        path = args.output_root / "preview" / args.run_id / f"{args.owner}.json"
        _write_preview(path, result)
    except (ValueError, OSError) as error:
        print(f"T1: {error}", file=sys.stderr)
        return 1
    data = result["data"]
    print(json.dumps({"status": result["status"], "papers": len(data["papers"]) if data else 0,
                      "attempts": result["attempts"], "preview": str(path)}, ensure_ascii=False))
    return {"success": 0, "partial": 2, "failed": 1}[result["status"]]
