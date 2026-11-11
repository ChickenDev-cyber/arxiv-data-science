"""Run Module 1 on one source folder, or on every version of an owner's corpus.

  one   --source-dir DIR --arxiv-id ID --version N [--output FILE.json]
  batch --corpus CORPUS --owner OWNER --output FILE.jsonl [--limit N] [--diagnostics FILE.jsonl]

batch reads CORPUS/raw/OWNER/<paper>/v<N>/ (the layout the M1 runner writes)
and prints how many versions got each status. --diagnostics writes one line per
version that needs a manual look (missing .bib/.tex, cycle, several roots).
"""

import argparse
import json
import os
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

from ..config import OWNERS
from .service import GatherReport, SourceGatherer

_VERSION_DIR = re.compile(r"v([1-9][0-9]*)")


def _atomic_write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, prefix=".gather-",
                                         delete=False) as stream:
            temporary = Path(stream.name)
            for line in lines:
                stream.write(line + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _dumps(value) -> str:
    # ensure_ascii keeps U+2028 and similar out of the file, so readers that
    # split lines on them cannot break a JSONL record.
    return json.dumps(value, ensure_ascii=True, allow_nan=False)


def paper_id_from_folder(paper_dir: Path) -> str:
    """arXiv ID of a raw/<owner>/<folder>/ directory.

    Prefer the ID stored in any version's source_manifest.json; otherwise
    reverse contracts.paper_folder ("." -> "-", "/" -> "__").
    """
    for manifest in sorted(paper_dir.glob("v*/source_manifest.json")):
        try:
            value = json.loads(manifest.read_text(encoding="utf-8")).get("arxiv_id")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(value, str):
            return value
    name = paper_dir.name
    if "__" in name:
        archive, number = name.split("__", 1)
        return f"{archive}/{number}"
    return name.replace("-", ".")


def iter_versions(corpus: Path, owner: str):
    """Yield (source_dir, arxiv_id, version) sorted by paper then version."""
    owner_dir = corpus / "raw" / owner
    if not owner_dir.is_dir():
        raise ValueError(f"no such folder: {owner_dir}")
    for paper_dir in sorted(p for p in owner_dir.iterdir() if p.is_dir()):
        versions = []
        for version_dir in paper_dir.iterdir():
            match = _VERSION_DIR.fullmatch(version_dir.name)
            if match and version_dir.is_dir():
                versions.append((int(match[1]), version_dir))
        if not versions:
            continue
        arxiv_id = paper_id_from_folder(paper_dir)
        for version, version_dir in sorted(versions):
            yield version_dir, arxiv_id, version


def _summary(report: GatherReport) -> dict:
    source = report.source
    return {
        "arxiv_id": source["arxiv_id"],
        "version": source["version"],
        "status": source["status"],
        "root_file": source["root_file"],
        "compilation_path": source["compilation_path"],
        "bib_files": source["bib_files"],
        "bbl_files": source["bbl_files"],
        "merged_chars": len(source["merged_tex"]),
        "root_candidates": [c.path for c in report.root_candidates],
        "missing_includes": report.missing_includes,
        "missing_bib": report.missing_bib,
        "cycles": report.cycles,
    }


def run_one(args, gatherer: SourceGatherer) -> int:
    if not args.source_dir.is_dir():
        print(f"source dir not found: {args.source_dir}", file=sys.stderr)
        return 1
    report = gatherer.inspect(args.source_dir, args.arxiv_id, args.version)
    print(json.dumps(_summary(report), ensure_ascii=False, indent=2))
    if args.output is not None:
        _atomic_write(args.output, [_dumps(report.source)])
        print(f"wrote {args.output}")
    return 0


def run_batch(args, gatherer: SourceGatherer) -> int:
    statuses: Counter = Counter()
    lines = []
    flagged = []
    with_missing = with_cycles = with_many_roots = with_bib = with_missing_bib = 0
    for count, (source_dir, arxiv_id, version) in enumerate(iter_versions(args.corpus, args.owner)):
        if args.limit is not None and count >= args.limit:
            break
        try:
            report = gatherer.inspect(source_dir, arxiv_id, version)
        except ValueError as exc:
            print(f"skip {source_dir}: {exc}", file=sys.stderr)
            statuses["skipped"] += 1
            continue
        statuses[report.source["status"]] += 1
        if (count + 1) % 500 == 0:
            print(f"  ... {count + 1} versions done", file=sys.stderr, flush=True)
        with_missing += bool(report.missing_includes)
        with_cycles += bool(report.cycles)
        with_many_roots += len(report.root_candidates) > 1
        with_bib += bool(report.source["bib_files"])
        with_missing_bib += bool(report.missing_bib)
        lines.append(_dumps(report.source))
        if (report.missing_bib or report.missing_includes or report.cycles
                or len(report.root_candidates) > 1):
            flagged.append(_dumps(_summary(report)))
    _atomic_write(args.output, lines)
    if args.diagnostics is not None:
        _atomic_write(args.diagnostics, flagged)
        print(f"wrote {len(flagged)} versions to look at in {args.diagnostics}")
    print(f"wrote {len(lines)} records to {args.output}")
    for status, number in sorted(statuses.items()):
        print(f"  {status}: {number}")
    print(f"  versions with missing \\input targets: {with_missing}")
    print(f"  versions with circular \\input: {with_cycles}")
    print(f"  versions with at least one .bib file: {with_bib}")
    print(f"  versions citing a .bib that is not on disk: {with_missing_bib}")
    print(f"  versions with more than one \\documentclass file: {with_many_roots}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    one = commands.add_parser("one", help="gather one paper version")
    one.add_argument("--source-dir", type=Path, required=True)
    one.add_argument("--arxiv-id", required=True)
    one.add_argument("--version", type=int, required=True)
    one.add_argument("--output", type=Path, help="write the GatheredSource as JSON")

    batch = commands.add_parser("batch", help="gather every version of one owner")
    batch.add_argument("--corpus", type=Path, required=True,
                       help="folder that holds raw/, e.g. data/m1-colab-huynh-nhi-01/corpus")
    batch.add_argument("--owner", required=True, choices=sorted(OWNERS))
    batch.add_argument("--output", type=Path, required=True, help="JSONL file to write")
    batch.add_argument("--limit", type=int, help="stop after N versions")
    batch.add_argument("--diagnostics", type=Path,
                       help="JSONL of versions with missing files, cycles or several roots")

    args = parser.parse_args(argv)
    gatherer = SourceGatherer()
    try:
        return run_one(args, gatherer) if args.command == "one" else run_batch(args, gatherer)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
