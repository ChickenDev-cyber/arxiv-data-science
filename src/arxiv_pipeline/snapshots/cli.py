"""Commands for moving a verified, stopped pipeline corpus between machines."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import tarfile

from .common import SnapshotLimits
from .service import SnapshotService


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="Pack a stopped run without modifying it")
    export.add_argument("--output-root", type=Path, required=True)
    export.add_argument("--run-id", required=True)
    export.add_argument("--destination", type=Path, required=True)
    export.add_argument("--snapshot-id", required=True)
    export.add_argument("--exporter-commit", required=True)
    for name in ("verify", "unpack", "restore"):
        command = commands.add_parser(name)
        command.add_argument("--archive", type=Path, required=True)
        if name != "verify":
            command.add_argument("--destination", type=Path, required=True)
    for command in (export, *[commands.choices[n] for n in ("verify", "unpack", "restore")]):
        command.add_argument("--max-files", type=int, default=100_000)
        command.add_argument("--max-file-bytes", type=int, default=2 * 1024**3)
        command.add_argument("--max-total-bytes", type=int, default=20 * 1024**3)
    return parser.parse_args(argv)


def make_service(command: str, limits: SnapshotLimits) -> SnapshotService:
    reader = relocator = None
    if command in ("export", "restore"):
        try:
            from ..collection.inspector import RunInspector
        except ModuleNotFoundError as exc:
            if exc.name not in ("arxiv_pipeline.collection", "arxiv_pipeline.collection.inspector"):
                raise
            raise ValueError(
                "Export/restore requires the shared collection.RunInspector (RunReader). "
                "Integrate that module before using these CLI commands."
            ) from exc
        reader = RunInspector()
        if command == "restore":
            from ..runner.restore import RunStateRelocator
            relocator = RunStateRelocator()
    return SnapshotService(reader, relocator, limits=limits)


def main(argv=None):
    args = parse_args(argv)
    try:
        limits = SnapshotLimits(args.max_files, args.max_file_bytes, args.max_total_bytes)
        service = make_service(args.command, limits)
        if args.command == "export":
            destination = service.export(
                args.output_root, args.run_id, args.destination,
                snapshot_id=args.snapshot_id, exporter_commit=args.exporter_commit,
            )
            report = {"snapshot_id": args.snapshot_id, "archive": str(destination)}
        else:
            method = getattr(service, args.command)
            manifest = (method(args.archive) if args.command == "verify"
                        else method(args.archive, args.destination))
            report = {"snapshot_id": manifest["snapshot_id"], "run_id": manifest["run_id"],
                      "is_mock": manifest["is_mock"], "files": len(manifest["files"])}
            if args.command != "verify":
                report["destination"] = str(args.destination)
                report["output_root"] = str(args.destination / "corpus")
        print(json.dumps(report, ensure_ascii=False))
        return 0
    except (ValueError, OSError, ImportError, EOFError, tarfile.TarError, sqlite3.Error) as exc:
        print(f"Snapshot: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
