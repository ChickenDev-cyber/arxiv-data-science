"""M3 Module A: inventory and normalize the five M1/M2 input tables per owner.

``prepare_inputs(source_config, output_dir)`` reads each owner's M1 corpus
(``metadata.json``/``references.json`` per paper) and the official M2 Parquet
export (elements, hierarchy, bibtex), checks them, and writes::

    <output_dir>/inputs/<owner>/<table>/part-*.parquet
    <output_dir>/inventory.json

Sources are only read, never modified. Missing metadata stays null.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import math
import random
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

import pyarrow as pa
import pyarrow.parquet as pq

from ..config import ASSIGNED_RANGES
from ..identifiers import normalize_arxiv_id
from .contracts import OWNERS, SCHEMAS as CONTRACT_SCHEMAS, TABLES
from .contracts import load_inventory as contract_load_inventory

INVENTORY_SCHEMA_VERSION = 1  # contracts.load_inventory (module E) requires 1
M2_TABLES = ("elements", "hierarchy", "bibtex")
SHARD_ROWS = 200_000
_STR = pa.string()
_DICT = pa.dictionary(pa.int32(), pa.string())
INT32_MIN, INT32_MAX = -(2 ** 31), 2 ** 31 - 1

# Output schemas come from module E's shared contract, never a private copy.
SCHEMAS: dict[str, pa.Schema] = {t: CONTRACT_SCHEMAS[t] for t in TABLES}

# Key columns; duplicates on these are collapsed (identical) or reported (conflicting).
KEYS: dict[str, tuple[str, ...]] = {
    "papers": ("arxiv_id",),
    "references": ("arxiv_id", "ref_arxiv_id"),
    "elements": ("element_id",),
    # M2's own primary key: one element may hang under several parents.
    "hierarchy": ("arxiv_id", "version", "element_id", "parent_id"),
    "bibtex": ("arxiv_id", "citation_key"),
}
NOT_NULL = {t: [f.name for f in s if not f.nullable] for t, s in SCHEMAS.items()}
# Columns holding paper IDs that must be canonical (version suffix removed).
ID_COLUMNS = {"papers": ("arxiv_id",), "references": ("arxiv_id", "ref_arxiv_id"),
              "elements": ("arxiv_id",), "hierarchy": ("arxiv_id",), "bibtex": ("arxiv_id",)}
INT_COLUMNS = {t: tuple(f.name for f in s if pa.types.is_integer(f.type)) for t, s in SCHEMAS.items()}
_INT_TEXT = re.compile(r"[+-]?[0-9]+")
_INVALID = object()


def safe_int(value: Any) -> Any:
    """Exact int32 conversion; returns _INVALID instead of truncating 1.9 to 1."""
    if value is None:
        return None
    if isinstance(value, bool):
        return _INVALID
    if isinstance(value, int):
        number = value
    elif isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return _INVALID
        number = int(value)
    elif isinstance(value, str) and _INT_TEXT.fullmatch(value.strip()):
        number = int(value.strip())
    else:
        return _INVALID
    return number if INT32_MIN <= number <= INT32_MAX else _INVALID


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _range_key(arxiv_id: str) -> tuple[int, int] | None:
    head, _, number = arxiv_id.partition(".")
    if len(head) != 4 or not head.isdigit() or not number.isdigit():
        return None
    return int(head), int(number)


def _owner_bounds(owner: str) -> tuple[tuple[int, int], tuple[int, int]]:
    cfg = next(c for c in ASSIGNED_RANGES if c["owner"] == owner)
    start = int(cfg["start_month"][2:4] + cfg["start_month"][5:7]), cfg["start_id"]
    end = int(cfg["end_month"][2:4] + cfg["end_month"][5:7]), cfg["end_id"]
    return start, end


def in_owner_range(owner: str, arxiv_id: str) -> bool:
    key = _range_key(arxiv_id)
    if key is None:
        return False
    start, end = _owner_bounds(owner)
    return start <= key <= end


def _folder_to_id(name: str) -> str | None:
    # Inverse of contracts.paper_folder: "2409-03594" / "hep-th__9901001".
    if "__" in name:
        archive, _, number = name.partition("__")
        return normalize_arxiv_id(f"{archive}/{number}")
    return normalize_arxiv_id(name.replace("-", "."))


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    return value or None


def _str_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return None
    return [s for s in (_str_or_none(v) for v in value) if s is not None]


def _digest(text: str) -> bytes:
    return hashlib.blake2b(text.encode("utf-8"), digest_size=16).digest()


def _resolve(base: Path, value: str | os.PathLike | None) -> Path | None:
    if value in (None, ""):
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_atomic(path: Path, data: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class Issues:
    """Collects counted issues for one owner/table; levels: error, warning."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], dict[str, Any]] = {}

    def add(self, level: str, code: str, message: str, count: int = 1,
            example: Any = None) -> None:
        item = self._items.setdefault((level, code), {
            "level": level, "code": code, "message": message, "count": 0, "examples": []})
        item["count"] += count
        if example is not None and len(item["examples"]) < 5:
            item["examples"].append(example)

    def has_errors(self) -> bool:
        return any(level == "error" for level, _ in self._items)

    def to_list(self) -> list[dict[str, Any]]:
        return [v for _, v in sorted(self._items.items())]


# ---------------------------------------------------------------------------
# Writer: shards, schema cast, null keys, duplicates, owner checks
# ---------------------------------------------------------------------------

class TableWriter:
    """Validates rows of one table for one owner and writes Parquet shards."""

    def __init__(self, table: str, owner: str, out_dir: Path, issues: Issues,
                 paper_ids: set[str] | None, shard_rows: int = SHARD_ROWS,
                 element_papers: dict[str, str] | None = None) -> None:
        self.table, self.owner, self.out_dir, self.issues = table, owner, out_dir, issues
        # hierarchy: element_id -> arxiv_id of accepted elements, to check both ends of an edge.
        self.element_papers = element_papers
        # elements: filled while writing, handed to the hierarchy writer.
        self.collected_elements: dict[str, str] = {}
        self.schema = SCHEMAS[table]
        self.paper_ids = paper_ids
        self.shard_rows = shard_rows
        # 16-byte blake2b digests of key -> row: collision-free in practice, low RAM.
        self.seen: dict[bytes, bytes] = {}
        self.rows = 0
        self.files: list[Path] = []
        self._writer: pq.ParquetWriter | None = None
        self._in_shard = 0
        self.arxiv_ids: set[str] = set()

    def _open_shard(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / f"part-{len(self.files):05d}.parquet"
        self._writer = pq.ParquetWriter(path, self.schema, compression="snappy")
        self.files.append(path)
        self._in_shard = 0

    def _clean(self, row: dict[str, Any]) -> dict[str, Any] | None:
        for col in ID_COLUMNS[self.table]:
            raw = row.get(col)
            norm = normalize_arxiv_id(raw)
            if norm is None:
                self.issues.add("error", f"invalid_{col}", f"{col} is null or not a valid arXiv ID; row dropped",
                                example=raw)
                return None
            if norm != raw:
                self.issues.add("warning", f"normalized_{col}",
                                f"{col} normalized (version suffix/prefix removed)", example=raw)
            row[col] = norm
        for col in INT_COLUMNS[self.table]:
            value = safe_int(row.get(col))
            if value is _INVALID:
                nullable = SCHEMAS[self.table].field(col).nullable
                self.issues.add("error", f"invalid_{col}",
                                f"{col} is not an exact int32"
                                + ("; set to null" if nullable else "; row dropped"),
                                example=repr(row.get(col)))
                if not nullable:
                    return None
                value = None
            row[col] = value
        for col in NOT_NULL[self.table]:
            if row.get(col) in (None, ""):
                self.issues.add("error", f"null_{col}", f"{col} is null/empty; row dropped",
                                example=row.get("arxiv_id"))
                return None
        aid = row["arxiv_id"]
        if not in_owner_range(self.owner, aid):
            self.issues.add("error", "foreign_owner",
                            f"arxiv_id outside the {self.owner} range; row dropped", example=aid)
            return None
        if self.paper_ids is not None and self.table != "papers" and aid not in self.paper_ids:
            self.issues.add("error", "paper_not_in_papers",
                            "citing arxiv_id is not in this owner's papers; row dropped", example=aid)
            return None
        if self.table == "hierarchy" and self.element_papers is not None:
            for col in ("element_id", "parent_id"):
                owner_paper = self.element_papers.get(row[col])
                if owner_paper is None:
                    self.issues.add("error", f"{col.split('_')[0]}_not_in_elements",
                                    f"hierarchy.{col} does not exist in elements; row dropped",
                                    example=row[col])
                    return None
                if owner_paper != aid:
                    self.issues.add("error", f"{col.split('_')[0]}_other_paper",
                                    f"hierarchy.{col} belongs to another paper; row dropped",
                                    example=[aid, row[col], owner_paper])
                    return None
        key = tuple(row.get(c) for c in KEYS[self.table])
        fingerprint = _digest(json.dumps(row, sort_keys=True, ensure_ascii=False, default=str))
        key_hash = _digest(json.dumps(key, ensure_ascii=False, default=str))
        previous = self.seen.get(key_hash)
        if previous is not None:
            if previous == fingerprint:
                self.issues.add("warning", "duplicate_identical",
                                f"identical duplicate on key {KEYS[self.table]}; extra copy dropped",
                                example=list(key))
            else:
                self.issues.add("error", "duplicate_conflict",
                                f"conflicting rows share key {KEYS[self.table]}; first kept",
                                example=list(key))
            return None
        self.seen[key_hash] = fingerprint
        if self.table == "elements":
            self.collected_elements[row["element_id"]] = sys.intern(aid)
        return row

    def write_rows(self, rows: Iterable[dict[str, Any]]) -> None:
        batch: list[dict[str, Any]] = []
        for row in rows:
            cleaned = self._clean(dict(row))
            if cleaned is None:
                continue
            batch.append(cleaned)
            if len(batch) >= 10_000:
                self._flush(batch)
                batch = []
        if batch:
            self._flush(batch)

    def _flush(self, batch: list[dict[str, Any]]) -> None:
        start = 0
        while start < len(batch):
            if self._writer is None or self._in_shard >= self.shard_rows:
                if self._writer is not None:
                    self._writer.close()
                self._open_shard()
            take = min(len(batch) - start, self.shard_rows - self._in_shard)
            chunk = batch[start:start + take]
            columns = {f.name: [r.get(f.name) for r in chunk] for f in self.schema}
            table = pa.Table.from_pydict(
                {name: pa.array(vals, type=_STR if self.schema.field(name).type == _DICT
                                else self.schema.field(name).type)
                 for name, vals in columns.items()})
            table = table.cast(self.schema)
            self._writer.write_table(table)
            self._in_shard += take
            self.rows += take
            self.arxiv_ids.update(r["arxiv_id"] for r in chunk)
            start += take

    def close(self) -> list[Path]:
        if self._writer is not None:
            self._writer.close()
        elif self.rows == 0:
            # Empty table still gets one shard with the right schema.
            self.out_dir.mkdir(parents=True, exist_ok=True)
            path = self.out_dir / "part-00000.parquet"
            pq.write_table(self.schema.empty_table(), path)
            self.files.append(path)
        return self.files


# ---------------------------------------------------------------------------
# M1 readers (extracted corpus directory or the Drive zip, read in place)
# ---------------------------------------------------------------------------

class M1Paper:
    """One paper folder of the M1 corpus, inside a directory or a zip archive."""

    def __init__(self, folder: str, reader: Callable[[str], bytes | None]) -> None:
        self.folder = folder
        self._reader = reader

    def read_json(self, name: str) -> Any:
        """Return parsed JSON, None if the file is absent; raise ValueError if broken."""
        raw = self._reader(name)
        if raw is None:
            return None
        return json.loads(raw.decode("utf-8"))


def _pick_papers_prefix(names: Iterable[str], owner: str) -> str | None:
    """Among '<prefix><folder>/metadata.json' paths, choose the prefix for this owner."""
    counts: dict[str, int] = {}
    for name in names:
        parts = name.split("/")
        if len(parts) >= 2 and parts[-1] in ("metadata.json", "references.json"):
            prefix = "/".join(parts[:-2])
            counts[prefix] = counts.get(prefix, 0) + 1
    if not counts:
        return None
    for wanted in (f"raw/{owner}", owner, "papers"):
        exact = [p for p in counts if p == wanted or p.endswith("/" + wanted)]
        if exact:
            return max(exact, key=lambda p: counts[p])
    # Never fall back to another owner's raw folder.
    others = [p for p in counts if not any(p.endswith(f"raw/{o}") for o in OWNERS if o != owner)]
    return max(others, key=lambda p: counts[p]) if others else None


def open_m1(source: Path, owner: str, owner_issues: Issues) -> tuple[list[M1Paper], str | None]:
    """List paper folders for one owner. Returns (papers, location description)."""
    if source.is_file() and source.suffix.lower() == ".zip":
        try:
            archive = zipfile.ZipFile(source)
        except (OSError, zipfile.BadZipFile) as exc:
            owner_issues.add("error", "m1_unreadable", f"M1 zip unreadable ({exc.__class__.__name__})",
                             example=str(source))
            return [], None
        names = [n for n in archive.namelist() if not n.endswith("/")]
        prefix = _pick_papers_prefix(names, owner)
        if prefix is None:
            nested = [n for n in names if n.endswith((".tar.gz", ".tar", ".zip"))]
            owner_issues.add("error", "m1_no_papers",
                             "no metadata.json/references.json in zip"
                             + (f"; it holds archives {nested[:3]}, extract them first" if nested else ""),
                             example=str(source))
            return [], None
        folders: dict[str, dict[str, str]] = {}
        base = prefix + "/" if prefix else ""
        for n in names:
            if n.startswith(base):
                rest = n[len(base):].split("/")
                if len(rest) == 2 and rest[1] in ("metadata.json", "references.json"):
                    folders.setdefault(rest[0], {})[rest[1]] = n
        papers = []
        for folder in sorted(folders):
            members = folders[folder]
            papers.append(M1Paper(folder, lambda name, m=members: archive.read(m[name]) if name in m else None))
        return papers, f"{source}!{prefix}"
    if not source.is_dir():
        owner_issues.add("error", "m1_not_found", "m1_corpus path does not exist", example=str(source))
        return [], None
    for candidate in (source / "raw" / owner, source / "corpus" / "raw" / owner, source / owner,
                      source / "papers", source):
        if candidate.is_dir() and any(
                (d / "metadata.json").is_file() or (d / "references.json").is_file()
                for d in candidate.iterdir() if d.is_dir()):
            papers = []
            for d in sorted(p for p in candidate.iterdir() if p.is_dir() and not p.name.startswith(".")):
                papers.append(M1Paper(d.name, lambda name, d=d: (d / name).read_bytes()
                                      if (d / name).is_file() else None))
            return papers, str(candidate)
    owner_issues.add("error", "m1_no_papers", "no paper folders under m1_corpus", example=str(source))
    return [], None


def resolve_m1_ids(papers: list[M1Paper], issues: Issues) -> list[tuple[str, M1Paper]]:
    result = []
    for paper in papers:
        aid = _folder_to_id(paper.folder)
        try:
            meta = paper.read_json("metadata.json")
            declared = normalize_arxiv_id(meta.get("arxiv_id")) if isinstance(meta, dict) else None
        except ValueError:
            declared = None
        if declared and aid and declared != aid:
            issues.add("warning", "folder_id_mismatch",
                       "metadata.json arxiv_id differs from folder name; metadata wins", example=paper.folder)
        aid = declared or aid
        if aid is None:
            issues.add("error", "unreadable_folder", "cannot derive arXiv ID from folder", example=paper.folder)
            continue
        result.append((aid, paper))
    return result


def papers_rows(dirs: list[tuple[str, M1Paper]], issues: Issues) -> Iterator[dict[str, Any]]:
    for aid, paper in dirs:
        data: dict[str, Any] = {}
        try:
            loaded = paper.read_json("metadata.json")
            if loaded is None:
                issues.add("warning", "metadata_missing",
                           "metadata.json missing; paper kept with null metadata", example=aid)
            elif not isinstance(loaded, dict):
                raise ValueError("not an object")
            else:
                data = loaded
        except ValueError as exc:
            issues.add("error", "metadata_unreadable",
                       f"metadata.json unreadable ({exc.__class__.__name__}); paper kept with null metadata",
                       example=aid)
        revised = _str_list(data.get("revised_dates"))
        yield {
            "arxiv_id": aid,
            "title": _str_or_none(data.get("title")),
            "authors": _str_list(data.get("authors")),
            "submission_date": _str_or_none(data.get("submission_date")),
            "revised_dates": revised,
            "venue": _str_or_none(data.get("venue")),
        }


def references_rows(dirs: list[tuple[str, M1Paper]], issues: Issues) -> Iterator[dict[str, Any]]:
    for aid, paper in dirs:
        try:
            data = paper.read_json("references.json")
        except ValueError:
            issues.add("error", "references_unreadable", "references.json unreadable", example=aid)
            continue
        if data is None:
            issues.add("warning", "references_missing", "references.json missing for paper", example=aid)
            continue
        if isinstance(data, dict) and "references" in data and isinstance(data["references"], dict):
            data = data["references"]  # whole ReferencesData object
        if isinstance(data, dict):
            items = list(data.items())
        elif isinstance(data, list):
            items = [(r.get("arxiv_id") if isinstance(r, dict) else None, r) for r in data]
        else:
            issues.add("error", "references_unreadable", "references.json has unexpected shape", example=aid)
            continue
        if not items:
            issues.add("warning", "references_empty", "paper has zero arXiv references", example=aid)
        for key, ref in items:
            ref = ref if isinstance(ref, dict) else {}
            yield {
                "arxiv_id": aid,
                "ref_arxiv_id": ref.get("arxiv_id") or key,
                "ref_title": _str_or_none(ref.get("title")),
                "ref_authors": _str_list(ref.get("authors")),
                "ref_submission_date": _str_or_none(ref.get("submission_date")),
                "ref_s2_id": _str_or_none(ref.get("semantic_scholar_id") or ref.get("s2_id")),
            }


# ---------------------------------------------------------------------------
# M2 run directory (milestone 2/<owner>/<run>/handoff.json)
# ---------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_m2_run(run_dir: Path, issues: Issues, verify: bool) -> tuple[Path | None, dict[str, Any]]:
    """Find the verified result generation of an M2 run; returns (parquet dir, info)."""
    info: dict[str, Any] = {"run_dir": str(run_dir), "handoff": None, "generation": None,
                            "checksums_verified": None}
    if not run_dir.is_dir():
        issues.add("error", "m2_not_found", "m2_run directory does not exist", example=str(run_dir))
        return None, info
    handoff_path = run_dir / "handoff.json"
    generation: Path | None = None
    handoff: dict[str, Any] = {}
    if handoff_path.is_file():
        try:
            handoff = _read_json(handoff_path)
        except (OSError, ValueError):
            issues.add("error", "m2_handoff_unreadable", "handoff.json unreadable", example=str(handoff_path))
        info["handoff"] = {"path": str(handoff_path), "status": handoff.get("status"),
                           "source_sha256": handoff.get("source_sha256")}
        if handoff.get("status") != "complete":
            issues.add("error", "m2_handoff_incomplete", "handoff.json status is not 'complete'",
                       example=handoff.get("status"))
        recorded = handoff.get("directory")
        if recorded:
            # The recorded path is absolute on the machine that wrote it; match by folder name.
            local = run_dir / "artifacts" / Path(recorded).name
            generation = local if local.is_dir() else (Path(recorded) if Path(recorded).is_dir() else None)
    else:
        issues.add("error", "m2_handoff_missing",
                   "handoff.json missing: run not verified; newest artifacts/result-* used", example=str(run_dir))
    if generation is None:
        candidates = sorted((d for d in (run_dir / "artifacts").glob("result-*") if (d / "parquet").is_dir()),
                            key=lambda d: d.stat().st_mtime)
        if not candidates:
            issues.add("error", "m2_no_parquet", "no artifacts/result-*/parquet in run", example=str(run_dir))
            return None, info
        generation = candidates[-1]
        if handoff:
            issues.add("error", "m2_generation_not_found",
                       "handoff generation folder not found; newest result-* used", example=str(generation))
    info["generation"] = str(generation)
    parquet = generation / "parquet"
    if verify and handoff.get("sha256"):
        bad = []
        checked = 0
        for name, expected in handoff["sha256"].items():
            if name.startswith("parquet/"):
                target = generation / name
                checked += 1
                if not target.is_file() or _sha256(target) != expected:
                    bad.append(name)
        info["checksums_verified"] = checked - len(bad)
        if bad:
            issues.add("error", "m2_checksum_mismatch", "Parquet file differs from handoff sha256",
                       len(bad), bad[0])
    return (parquet if parquet.is_dir() else generation), info


# ---------------------------------------------------------------------------
# Parquet readers (M2 export or prebuilt papers/references)
# ---------------------------------------------------------------------------

def parquet_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(p for p in path.rglob("*.parquet") if p.is_file())


def find_m2_table(m2_dir: Path, table: str) -> Path | None:
    for candidate in (m2_dir / table, m2_dir / f"{table}.parquet",
                      m2_dir / "parquet" / table, m2_dir / "parquet" / f"{table}.parquet"):
        if candidate.exists():
            return candidate
    return None


def parquet_rows(files: list[Path], table: str, issues: Issues) -> Iterator[dict[str, Any]]:
    wanted = [f.name for f in SCHEMAS[table]]
    for file in files:
        try:
            pf = pq.ParquetFile(file)
        except Exception as exc:  # pyarrow raises several exception types
            issues.add("error", "parquet_unreadable", f"cannot open Parquet ({exc.__class__.__name__})",
                       example=str(file))
            continue
        present = set(pf.schema_arrow.names)
        missing = [c for c in wanted if c not in present]
        if missing:
            issues.add("error", "schema_missing_columns",
                       f"file lacks columns {missing}; file skipped", example=str(file))
            continue
        extra = sorted(present - set(wanted))
        if extra:
            issues.add("warning", "schema_extra_columns", f"extra columns ignored: {extra}",
                       example=str(file))
        for batch in pf.iter_batches(columns=wanted, batch_size=20_000):
            for row in batch.to_pylist():
                if table == "bibtex" and isinstance(row.get("authors"), str):
                    row["authors"] = _str_list(json.loads(row["authors"]) if row["authors"].startswith("[")
                                               else [row["authors"]])
                yield row


# ---------------------------------------------------------------------------
# Source identity (snapshot / run)
# ---------------------------------------------------------------------------

_IDENTITY_FILES = ("handoff.json", "run.json", "manifest.json", "complete.json", "benchmark.json")
_IDENTITY_KEYS = ("run_id", "snapshot_id", "collection_id", "commit", "commit_sha", "source_sha256",
                  "code_sha256", "owner", "status", "created_at")


def describe_source(path: Path | None, label: str | None) -> dict[str, Any]:
    info: dict[str, Any] = {"path": str(path) if path else None, "label": label, "identity_files": {}}
    if path is None:
        return info
    probe = path if path.is_dir() else path.parent
    for level, directory in enumerate([probe, *probe.parents][:5]):
        for name in _IDENTITY_FILES:
            f = directory / name
            if f.is_file() and name not in info["identity_files"]:
                try:
                    data = _read_json(f)
                except (OSError, ValueError):
                    continue
                if isinstance(data, dict):
                    info["identity_files"][name] = {
                        "path": str(f), **{k: data[k] for k in _IDENTITY_KEYS if k in data}}
    if label is None:
        for meta in info["identity_files"].values():
            if meta.get("run_id") or meta.get("snapshot_id"):
                info["label"] = meta.get("run_id") or meta.get("snapshot_id")
                break
    if info["label"] is None:
        info["label"] = path.stem if path.is_file() else path.name
    if path.is_file():
        stat = path.stat()
        info["size_bytes"] = stat.st_size
        info["modified_at"] = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds")
    return info


# ---------------------------------------------------------------------------
# Relations
# ---------------------------------------------------------------------------

def _column_values(files: list[Path], column: str) -> set[str]:
    values: set[str] = set()
    for f in files:
        values.update(v for v in pq.read_table(f, columns=[column]).column(column).to_pylist() if v)
    return values


def check_relations(tables: dict[str, dict[str, Any]], issues: dict[str, Issues]) -> None:
    def files(t: str) -> list[Path]:
        return tables[t].get("_files", [])

    if not files("papers"):
        return
    paper_ids = _column_values(files("papers"), "arxiv_id")
    # hierarchy edges are checked row by row in TableWriter (both ends, same paper).
    for t in ("elements", "bibtex", "references"):
        if files(t):
            ids = _column_values(files(t), "arxiv_id")
            tables[t]["papers_covered"] = len(ids & paper_ids)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _status(issues: Issues, rows: int, missing: bool) -> str:
    if missing:
        return "missing"
    if issues.has_errors():
        return "error"
    return "empty" if rows == 0 else "ok"


def _rel(path: Path, base: Path) -> str:
    return Path(os.path.relpath(path, base)).as_posix()


def load_source_config(source_config: Path) -> tuple[Path, dict[str, Any]]:
    source_config = Path(source_config)
    config = _read_json(source_config)
    if not isinstance(config, dict) or not isinstance(config.get("owners"), dict):
        raise ValueError("source config must be a JSON object with an 'owners' object")
    unknown = sorted(set(config["owners"]) - set(OWNERS))
    if unknown:
        raise ValueError(f"unknown owners in source config: {unknown}")
    return source_config.resolve().parent, config


def _issue_lines(items: list[dict[str, Any]], level: str, prefix: str = "") -> list[str]:
    return [f"{prefix}{i['code']} x{i['count']}: {i['message']}" for i in items if i["level"] == level]


def _prepare_owner(owner: str, spec: dict[str, Any], base: Path, out_root: Path,
                   inventory_dir: Path, shard_rows: int, verify_checksums: bool) -> dict[str, Any]:
    m1_corpus = _resolve(base, spec.get("m1_corpus"))
    m2_run = _resolve(base, spec.get("m2_run_dir"))
    m2_dir = _resolve(base, spec.get("m2_parquet"))
    prebuilt = {t: _resolve(base, spec.get(t)) for t in TABLES}
    issues = {t: Issues() for t in TABLES}
    owner_issues = Issues()
    tables: dict[str, dict[str, Any]] = {}
    papers_location = None
    dirs: list[tuple[str, M1Paper]] = []
    m2_info: dict[str, Any] | None = None

    # M1 is only needed for papers/references that are not given as Parquet.
    needs_m1 = prebuilt["papers"] is None or prebuilt["references"] is None
    if m1_corpus is not None:
        papers, papers_location = open_m1(m1_corpus, owner, owner_issues)
        dirs = resolve_m1_ids(papers, issues["papers"])
    elif needs_m1:
        owner_issues.add("error", "m1_not_configured",
                         "m1_corpus not set and papers/references Parquet not given")

    needs_m2 = any(prebuilt[t] is None for t in M2_TABLES)
    if m2_dir is None and m2_run is not None and needs_m2:
        m2_dir, m2_info = resolve_m2_run(m2_run, owner_issues, verify_checksums)

    def run(table: str, rows: Iterator[dict[str, Any]] | None, source: list[Path] | str | None,
            paper_ids: set[str] | None, element_papers: dict[str, str] | None = None) -> TableWriter:
        writer = TableWriter(table, owner, out_root / table, issues[table], paper_ids, shard_rows,
                             element_papers)
        missing = rows is None
        if not missing:
            writer.write_rows(rows)
        written = writer.close() if not missing else []
        tables[table] = {
            "status": _status(issues[table], writer.rows, missing),
            "rows": writer.rows if not missing else None,
            "paths": [_rel(p, inventory_dir) for p in written],
            "source": [str(s) for s in source] if isinstance(source, list) else source,
            "_files": written,
        }
        return writer

    # papers / references: prebuilt Parquet if configured, else converted from M1.
    paper_ids: set[str] | None = None
    for table, builder in (("papers", papers_rows), ("references", references_rows)):
        given = prebuilt[table]
        if given is not None:
            files = parquet_files(given) if given.exists() else []
            if not files:
                issues[table].add("error", "source_not_found", "configured Parquet not found", example=str(given))
                writer = run(table, None, str(given), paper_ids)
            else:
                writer = run(table, parquet_rows(files, table, issues[table]), files, paper_ids)
        elif papers_location is not None:
            writer = run(table, builder(dirs, issues[table]), papers_location, paper_ids)
        else:
            issues[table].add("error", "source_not_found", "no M1 corpus and no prebuilt Parquet")
            writer = run(table, None, None, paper_ids)
        if table == "papers" and tables["papers"]["status"] != "missing":
            paper_ids = writer.arxiv_ids

    element_papers: dict[str, str] | None = None
    for table in M2_TABLES:
        explicit = prebuilt[table]
        located = explicit if explicit is not None else (find_m2_table(m2_dir, table) if m2_dir else None)
        if located is None or not located.exists():
            issues[table].add("error", "source_not_found",
                              "M2 table not found" if m2_dir or explicit
                              else "neither m2_run_dir nor m2_parquet usable in source config",
                              example=str(located or m2_dir) if (located or m2_dir) else None)
            run(table, None, str(located) if located else None, paper_ids)
            continue
        files = parquet_files(located)
        if not files:
            issues[table].add("error", "source_not_found", "no .parquet files in M2 table path",
                              example=str(located))
            run(table, None, str(located), paper_ids)
            continue
        if table == "hierarchy" and element_papers is None:
            issues[table].add("error", "relations_unchecked",
                              "elements unavailable, hierarchy edges cannot be verified")
        writer = run(table, parquet_rows(files, table, issues[table]), files, paper_ids,
                     element_papers if table == "hierarchy" else None)
        if table == "elements":
            element_papers = writer.collected_elements
    element_papers = None  # free memory before relation summary

    # N is the number of distinct paper IDs actually written to papers.
    n_papers = tables["papers"]["rows"] or 0
    if dirs and tables["papers"]["status"] != "missing" and len(dirs) != n_papers:
        owner_issues.add("error", "n_mismatch",
                         f"M1 has {len(dirs)} paper folders but papers has {n_papers} IDs")

    check_relations(tables, issues)
    for t in TABLES:
        tables[t]["issues"] = issues[t].to_list()
        if tables[t]["status"] == "ok" and issues[t].has_errors():
            tables[t]["status"] = "error"
        tables[t].pop("_files")

    statuses = {t: tables[t]["status"] for t in TABLES}
    m1_info = describe_source(m1_corpus, spec.get("m1_snapshot")) if m1_corpus else None
    m2_desc = ({**describe_source(m2_run or m2_dir, spec.get("m2_label")), "resolved": m2_info,
                "parquet_dir": str(m2_dir) if m2_dir else None} if (m2_run or m2_dir) else None)
    snapshot = spec.get("snapshot") or " + ".join(
        f"{name}:{info['label']}" for name, info in (("m1", m1_info), ("m2", m2_desc)) if info) or "prebuilt-parquet"
    all_issues = owner_issues.to_list() + [
        {**i, "code": f"{t}.{i['code']}"} for t in TABLES for i in tables[t]["issues"]]
    return {
        "N": n_papers,
        "snapshot": snapshot,
        "tables": {t: tables[t]["paths"] for t in TABLES},
        # Only real counts: module E rejects null and compares every value with the data.
        "row_counts": {t: tables[t]["rows"] for t in TABLES if tables[t]["rows"] is not None},
        "warnings": _issue_lines(all_issues, "warning"),
        "errors": _issue_lines(all_issues, "error"),
        "status": "ok" if all(s == "ok" for s in statuses.values()) and not owner_issues.has_errors()
        else ("missing" if all(s == "missing" for s in statuses.values()) else "incomplete"),
        "table_status": statuses,
        "m1_paper_folders": len(dirs) if m1_corpus else None,
        "sources": {"m1": m1_info, "m2": m2_desc},
        "table_details": {t: {k: v for k, v in tables[t].items() if k != "paths"} for t in TABLES},
        "issues": owner_issues.to_list(),
    }


def _check_output_dir(output_dir: Path, sources: list[Path]) -> None:
    inputs = output_dir / "inputs"
    if (output_dir / "inventory.json").exists() or (inputs.exists() and any(inputs.iterdir())):
        raise FileExistsError(f"{output_dir} already has prepared inputs; use a new run directory")
    out = output_dir.resolve()
    for src in sources:
        s = src.resolve()
        if out == s or s in out.parents or out in s.parents:
            raise ValueError(f"output_dir {out} overlaps source {s}; M1/M2 inputs must not be written")


def prepare_inputs(source_config: Path, output_dir: Path, *, owners: Iterable[str] | None = None,
                   shard_rows: int = SHARD_ROWS, verify_checksums: bool = True) -> Path:
    """Inventory, validate and normalize M1/M2 inputs; return the inventory.json path."""
    base, config = load_source_config(Path(source_config))
    output_dir = Path(output_dir)
    selected = list(owners) if owners else [o for o in OWNERS if o in config["owners"]]
    bad = [o for o in selected if o not in OWNERS]
    if bad:
        raise ValueError(f"unknown owners: {bad}")
    sources = [p for o in selected for k in ("m1_corpus", "m2_run_dir", "m2_parquet", *TABLES)
               if (p := _resolve(base, config["owners"].get(o, {}).get(k))) is not None and p.exists()]
    _check_output_dir(output_dir, sources)
    output_dir.mkdir(parents=True, exist_ok=True)

    inventory: dict[str, Any] = {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "generator": "arxiv_pipeline.matching.data.prepare_inputs",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_config": str(Path(source_config).resolve()),
        "paths_relative_to": "inventory.json directory",
        "schemas": {t: [f"{f.name}: {f.type}{'' if f.nullable else ' not null'}" for f in s]
                    for t, s in SCHEMAS.items()},
        "keys": {t: list(k) for t, k in KEYS.items()},
        "owners": {},
    }
    for owner in OWNERS:
        if owner not in selected:
            continue
        spec = config["owners"].get(owner)
        if spec is None:
            inventory["owners"][owner] = {
                "N": 0, "snapshot": "", "tables": {t: [] for t in TABLES}, "row_counts": {},
                "warnings": [], "errors": ["owner_not_configured: owner absent from source config"],
                "status": "missing", "table_status": {t: "missing" for t in TABLES},
                "issues": [{"level": "error", "code": "owner_not_configured",
                            "message": "owner absent from source config", "count": 1, "examples": []}]}
            continue
        inventory["owners"][owner] = _prepare_owner(
            owner, spec, base, output_dir / "inputs" / owner, output_dir, shard_rows, verify_checksums)
    path = output_dir / "inventory.json"
    _write_json_atomic(path, inventory)
    return path


def load_inventory(inventory: Path) -> dict[str, Any]:
    """Read inventory.json and turn every table path into an absolute path string."""
    inventory = Path(inventory)
    data = _read_json(inventory)
    base = inventory.resolve().parent
    for info in data.get("owners", {}).values():
        info["tables"] = {t: [str((base / p).resolve()) for p in paths]
                          for t, paths in info.get("tables", {}).items()}
    return data


def read_table(inventory: Path, table: str, owner: str | None = None) -> pa.Table:
    """Concatenate one table across owners (or one owner) from an inventory."""
    data = load_inventory(inventory)
    parts = [pq.read_table(p, schema=SCHEMAS[table])
             for o, info in data["owners"].items() if owner in (None, o)
             for p in info["tables"].get(table, [])]
    return pa.concat_tables(parts) if parts else SCHEMAS[table].empty_table()


def make_sample(inventory: Path, output_dir: Path, *, papers_per_owner: int = 3,
                seed: int = 42) -> Path:
    """Copy all rows of a few real papers per owner into a small, portable inventory.

    Prefers papers that have both bibtex entries and references so downstream
    modules can test matching. Paths in the sample inventory are relative.
    """
    data = load_inventory(Path(inventory))
    output_dir = Path(output_dir)
    _check_output_dir(output_dir, [])
    rng = random.Random(seed)
    sample: dict[str, Any] = {**{k: v for k, v in data.items() if k != "owners"},
                              "sample_of": str(Path(inventory).resolve()),
                              "sample_papers_per_owner": papers_per_owner, "sample_seed": seed,
                              "owners": {}}
    sample["created_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for owner, info in data["owners"].items():
        paths = info.get("tables", {})

        def load(t: str) -> pa.Table:
            files = paths.get(t, [])
            return (pa.concat_tables([pq.read_table(p, schema=SCHEMAS[t]) for p in files])
                    if files else SCHEMAS[t].empty_table())

        loaded = {t: load(t) for t in TABLES}
        bib_ids = set(loaded["bibtex"].column("arxiv_id").to_pylist())
        ref_ids = set(loaded["references"].column("arxiv_id").to_pylist())
        all_ids = sorted(set(loaded["papers"].column("arxiv_id").to_pylist()))
        preferred = sorted(bib_ids & ref_ids & set(all_ids)) or sorted(bib_ids | ref_ids) or all_ids
        chosen = sorted(rng.sample(preferred, min(papers_per_owner, len(preferred))))
        chosen_set = set(chosen)
        # N counts the sample's own papers so module E's contract check passes on it.
        owner_info: dict[str, Any] = {"N": len(chosen),
                                      "snapshot": f"{info.get('snapshot')} (sample {len(chosen)} papers)",
                                      "tables": {}, "row_counts": {}, "warnings": [], "errors": [],
                                      "status": info.get("status"), "sample_arxiv_ids": chosen,
                                      "full_N": info.get("N"), "table_status": {}}
        for t in TABLES:
            mask = [a in chosen_set for a in loaded[t].column("arxiv_id").to_pylist()]
            sub = loaded[t].filter(pa.array(mask, type=pa.bool_())) if mask else loaded[t]
            target = output_dir / "inputs" / owner / t
            target.mkdir(parents=True, exist_ok=True)
            pq.write_table(sub.cast(SCHEMAS[t]), target / "part-00000.parquet")
            owner_info["tables"][t] = [_rel(target / "part-00000.parquet", output_dir)]
            owner_info["row_counts"][t] = sub.num_rows
            owner_info["table_status"][t] = (info.get("table_status", {}).get(t)
                                             if sub.num_rows or not chosen else "empty")
        sample["owners"][owner] = owner_info
    path = output_dir / "inventory.json"
    _write_json_atomic(path, sample)
    return path


def scan_drive(root: Path, m1_folder: str = "Full-data", m2_folder: str = "milestone 2") -> dict[str, Any]:
    """Draft a source config from the team Drive layout; choose the newest complete M2 run.

    Review the result before using it: the official run is a team decision.
    """
    root = Path(root)
    config: dict[str, Any] = {"owners": {}, "notes": {}}
    for owner in OWNERS:
        spec: dict[str, Any] = {}
        notes: list[str] = []
        zips = sorted((root / m1_folder).glob(f"m1-colab-{owner}-*.zip"))
        if zips:
            spec["m1_corpus"] = str(zips[-1])
            if len(zips) > 1:
                notes.append(f"several M1 zips, newest name chosen: {[z.name for z in zips]}")
        else:
            notes.append(f"no {m1_folder}/m1-colab-{owner}-*.zip")
        runs = []
        owner_dir = root / m2_folder / owner
        for run in sorted(owner_dir.iterdir()) if owner_dir.is_dir() else []:
            handoff = run / "handoff.json"
            status = None
            if handoff.is_file():
                try:
                    status = _read_json(handoff).get("status")
                except (OSError, ValueError):
                    status = "unreadable"
            runs.append({"run": run.name, "handoff_status": status,
                         "modified": handoff.stat().st_mtime if handoff.is_file() else 0})
        complete = [r for r in runs if r["handoff_status"] == "complete"]
        if complete:
            best = max(complete, key=lambda r: r["modified"])
            spec["m2_run_dir"] = str(owner_dir / best["run"])
        elif runs:
            notes.append("no M2 run with a complete handoff.json; set m2_run_dir by hand")
        else:
            notes.append(f"no {m2_folder}/{owner}/<run> folders")
        if len(runs) > 1:
            notes.append("M2 runs found: " + ", ".join(f"{r['run']}({r['handoff_status']})" for r in runs))
        config["owners"][owner] = spec
        if notes:
            config["notes"][owner] = notes
    return config


def summarize(inventory: Path) -> str:
    data = _read_json(Path(inventory))
    lines = [f"{'owner':<10} {'status':<11} {'N':>6}  " + "  ".join(f"{t:>18}" for t in TABLES)]
    for owner, info in data["owners"].items():
        cells = []
        for t in TABLES:
            rows = info.get("row_counts", {}).get(t)
            cells.append(f"{(info.get('table_status', {}).get(t) or '?') + ':' + ('-' if rows is None else str(rows)):>18}")
        n = info.get("N")
        lines.append(f"{owner:<10} {str(info.get('status')):<11} {('-' if n is None else n):>6}  " + "  ".join(cells))
        problems = [*info.get("issues", [])]
        for t, d in info.get("table_details", {}).items():
            problems += [{**i, "code": f"{t}.{i['code']}"} for i in d.get("issues", [])]
        for i in problems:
            lines.append(f"    [{i['level']}] {i['code']} x{i['count']}: {i['message']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m arxiv_pipeline.matching.data",
                                     description="M3 Module A: prepare the five input tables")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare", help="inventory + normalize M1/M2 into inputs/<owner>/<table>")
    p.add_argument("--config", required=True, type=Path, help="source config JSON")
    p.add_argument("--output-dir", required=True, type=Path, help="new run directory")
    p.add_argument("--owners", help="comma-separated subset, default: all in config")
    p.add_argument("--no-verify-checksums", action="store_true",
                   help="skip re-hashing M2 Parquet against handoff.json")
    s = sub.add_parser("sample", help="copy a few real papers per owner into a small inventory")
    s.add_argument("--inventory", required=True, type=Path)
    s.add_argument("--output-dir", required=True, type=Path)
    s.add_argument("--papers", type=int, default=3)
    s.add_argument("--seed", type=int, default=42)
    d = sub.add_parser("scan", help="draft a source config from the team Drive folder")
    d.add_argument("--drive-root", required=True, type=Path, help="folder holding Full-data/ and milestone 2/")
    d.add_argument("--write", required=True, type=Path, help="where to write the draft config JSON")
    r = sub.add_parser("report", help="print a summary of an inventory.json")
    r.add_argument("--inventory", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "scan":
            draft = scan_drive(args.drive_root)
            if args.write.exists():
                raise FileExistsError(f"{args.write} exists; choose another name")
            _write_json_atomic(args.write, draft)
            print(json.dumps(draft, ensure_ascii=False, indent=2))
            return 0
        if args.command == "prepare":
            owners = [o.strip() for o in args.owners.split(",")] if args.owners else None
            path = prepare_inputs(args.config, args.output_dir, owners=owners,
                                  verify_checksums=not args.no_verify_checksums)
        elif args.command == "sample":
            path = make_sample(args.inventory, args.output_dir, papers_per_owner=args.papers, seed=args.seed)
        else:
            print(summarize(args.inventory))
            return 0
    except (ValueError, FileExistsError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(summarize(path))
    print(f"\ninventory: {path}")
    data = _read_json(path)
    ok = all(o.get("status") == "ok" for o in data["owners"].values())
    if ok:
        try:
            contract_load_inventory(path)
            print("module E contract (contracts.load_inventory): OK")
        except ValueError as exc:
            print(f"module E contract (contracts.load_inventory): FAILED: {exc}", file=sys.stderr)
            return 2
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
