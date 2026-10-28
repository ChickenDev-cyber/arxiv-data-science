"""Inspect a stopped run, assemble snapshots into a collection, or check a collection."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from ..config import ASSIGNED_RANGES
from .assembler import CollectionAssembler
from .reader import CollectionDirectoryReader


def _issues(quality, snapshot_id=None):
    extra = {"snapshot_id": snapshot_id} if snapshot_id else {}
    return [{**extra, **{k: i[k] for k in ("owner", "arxiv_id", "version", "step", "message")}}
            for i in quality["issues"]]


def _run_summary(run):
    return {"run_id": run.scope["run_id"], "run_state": run.run_state, "is_mock": run.scope["is_mock"],
            "owners": sorted(c["owner"] for c in run.scope["scope"]["ranges"]),
            "papers": run.scope["scope"]["paper_count"], "accepted_results": len(run.accepted_results),
            "files": len(run.files), "quality_valid": run.quality["valid"]}


def inspect(args):
    # Imported here: the runner state store needs fcntl, which Windows lacks.
    from .inspector import RunInspector
    with RunInspector().open(args.output_root, args.run_id) as run:
        report = {**_run_summary(run), "issues": _issues(run.quality)}
    return report, 0 if run.quality["valid"] else 2


def assemble(args):
    assembler = CollectionAssembler()
    result = assembler.assemble(args.root, args.collection_id, args.snapshots)
    path = CollectionAssembler.save(args.root, result.manifest)
    return {"collection_id": args.collection_id, "is_mock": result.manifest["is_mock"],
            "members": [{"snapshot_id": m["snapshot_id"], "owners": m["owners"]} for m in result.manifest["members"]],
            "skipped": result.skipped, "manifest": str(path)}, 0


def check(args):
    from .inspector import RunInspector
    with CollectionDirectoryReader(RunInspector()).open(args.root) as collection:
        members, issues = [], []
        for member, run in zip(collection.manifest["members"], collection.runs):
            members.append({"snapshot_id": member["snapshot_id"], **_run_summary(run)})
            issues += _issues(run.quality, member["snapshot_id"])
        present = {o for m in collection.manifest["members"] for o in m["owners"]}
        missing = [c["owner"] for c in ASSIGNED_RANGES if c["owner"] not in present]
        report = {"collection_id": collection.manifest["collection_id"], "is_mock": collection.manifest["is_mock"],
                  "members": members, "missing_owners": missing, "issues": issues}
    return report, 0 if not issues and not missing else 2


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m arxiv_pipeline.collection", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("inspect", help="verify one stopped run read-only and list its quality issues")
    p.add_argument("--output-root", type=Path, required=True)
    p.add_argument("--run-id", required=True)
    p.set_defaults(handler=inspect)
    p = commands.add_parser("assemble", help="choose one unpacked snapshot per owner and write collection.json")
    p.add_argument("--root", type=Path, required=True, help="collection folder that contains the snapshots")
    p.add_argument("--collection-id", required=True)
    p.add_argument("snapshots", nargs="+", help="snapshot folders relative to --root, each with manifest.json next to corpus/")
    p.set_defaults(handler=assemble)
    p = commands.add_parser("check", help="re-verify every snapshot and run of a collection; list all issues")
    p.add_argument("--root", type=Path, required=True)
    p.set_defaults(handler=check)
    args = parser.parse_args(argv)
    try:
        report, code = args.handler(args)
    except ImportError as error:
        print(f"collection: {args.command} needs the runner state store, which requires Linux/macOS "
              f"(Colab works): {error}", file=sys.stderr)
        return 1
    except (ValueError, OSError, sqlite3.Error) as error:
        print(f"collection: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return code
