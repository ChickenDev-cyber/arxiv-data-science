"""Run a T2 metadata preview for one paper and write its Result as JSON."""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from ..config import OWNERS
from ..contracts import paper_folder
from ..discovery.service import validate_context
from .api import FileClient, OaiClient
from .service import MetadataModule, validate_task


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
    parser.add_argument("--arxiv-id", required=True)
    parser.add_argument("--run-id", default="t2-preview")
    parser.add_argument("--output-root", type=Path, default=Path("/tmp/arxiv-t2"))
    parser.add_argument("--fixture", type=Path,
                        help="read a saved arXivRaw response instead of calling arXiv (is_mock=true)")
    args = parser.parse_args(argv)
    task = dict(owner=args.owner, arxiv_id=args.arxiv_id)
    context = dict(run_id=args.run_id, is_mock=args.fixture is not None, output_root=args.output_root)
    try:
        validate_task(task)
        validate_context(context)
        client = FileClient({args.arxiv_id: args.fixture}) if args.fixture else OaiClient()
        result = MetadataModule(client).fetch_metadata(task, context)
        path = args.output_root / "preview" / args.run_id / "metadata" / f"{paper_folder(args.arxiv_id)}.json"
        _write_preview(path, result)
    except (ValueError, OSError) as error:
        print(f"T2: {error}", file=sys.stderr)
        return 1
    data = result["data"]
    print(json.dumps({"status": result["status"],
                      "versions": [v["version"] for v in data["versions"]] if data else [],
                      "missing_fields": result["missing_fields"],
                      "attempts": result["attempts"], "preview": str(path)}, ensure_ascii=False))
    return {"success": 0, "partial": 2, "not_found": 3, "failed": 1}[result["status"]]
