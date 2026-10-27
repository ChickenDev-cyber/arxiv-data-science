"""Run T4 for one paper and write its Result as a JSON preview (not the official corpus)."""

import argparse
import json
import sys
from pathlib import Path

from ..config import OWNERS
from ..contracts import paper_folder
from ..discovery.cli import _write_preview
from ..http import RequestScheduler
from .checkpoint import JsonFileCheckpointStore
from .api import ArxivDateClient, SemanticScholarClient
from .service import ReferencesModule, validate_task
from ..discovery.service import validate_context


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True, choices=sorted(OWNERS),
                        help="owner of the range that contains this paper (taken from the task, never assumed)")
    parser.add_argument("--arxiv-id", required=True, help="canonical ID, e.g. 2501.12642 (no URL, no vN)")
    parser.add_argument("--run-id", default="t4-preview")
    parser.add_argument("--output-root", type=Path, default=Path("/tmp/arxiv-t4"))
    parser.add_argument("--state-dir", type=Path, default=None,
                        help="optional folder for checkpoints: resume reference pages, cache submission dates "
                             "and keep the API waiting schedule across restarts")
    args = parser.parse_args(argv)
    task = {"owner": args.owner, "arxiv_id": args.arxiv_id}
    context = dict(run_id=args.run_id, is_mock=False, output_root=args.output_root)
    try:
        validate_task(task)
        validate_context(context)
        if args.state_dir is None:
            module = ReferencesModule(SemanticScholarClient(), ArxivDateClient())
        else:
            store = JsonFileCheckpointStore(args.state_dir)
            scheduler = RequestScheduler(checkpoints=store)
            module = ReferencesModule(SemanticScholarClient(scheduler=scheduler),
                                      ArxivDateClient(scheduler=scheduler), checkpoints=store)
        result = module.fetch_references(task, context)
        path = (args.output_root / "preview" / args.run_id /
                f"{args.owner}-{paper_folder(args.arxiv_id)}.references.json")
        _write_preview(path, result)
    except (ValueError, OSError) as error:
        print(f"T4: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"status": result["status"], "complete": (result["data"] or {}).get("complete"),
                      "metrics": result["metrics"], "attempts": result["attempts"],
                      "preview": str(path)}, ensure_ascii=False))
    return {"success": 0, "empty": 0, "partial": 2, "failed": 1, "not_found": 1}[result["status"]]
