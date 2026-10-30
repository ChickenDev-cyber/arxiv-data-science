"""Inspect a stopped run, assemble snapshots into a collection, or check a collection."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .assembler import CollectionAssembler
from .reader import CollectionDirectoryReader
from .report import assembly_report, collection_report, run_report


def inspect(args):
    # Imported here: the runner state store needs fcntl, which Windows lacks.
    from .inspector import RunInspector
    with RunInspector().open(args.output_root, args.run_id) as run:
        return run_report(run)


def assemble(args):
    manifest = CollectionAssembler().assemble(args.root, args.collection_id, args.snapshots)
    return assembly_report(manifest, CollectionAssembler.save(args.root, manifest))


def check(args):
    from .inspector import RunInspector
    with CollectionDirectoryReader(RunInspector()).open(args.root) as collection:
        return collection_report(collection)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m arxiv_pipeline.collection", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("inspect", help="verify one stopped run read-only and list its quality issues")
    p.add_argument("--output-root", type=Path, required=True)
    p.add_argument("--run-id", required=True)
    p.set_defaults(handler=inspect)
    p = commands.add_parser("assemble", help="write collection.json for unpacked snapshots, one per owner")
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
