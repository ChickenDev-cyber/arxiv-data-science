"""Semantic checks for operational documents of pipeline_contracts; no I/O.

``validate_pipeline_document`` returns readable problems as ``list[str]`` so the
runner can pass it to checkpoint stores as a ``CheckpointValidator``.
"""
import json
import re
from typing import get_type_hints

from ..identifiers import is_valid_arxiv_id
from ..json_utils import canonical
from ..pipeline_contracts import (ArxivDateCheckpoint, CheckpointRecord, CollectionManifest,
                                  CollectionStatistics, DiscoveryBatchCheckpoint, RateLimitCheckpoint,
                                  ReferencesCheckpoint, SnapshotManifest)
from ..references.api import valid_iso_date
from ..contracts import Summary
from .results import _check, utc

DOCUMENTS = {"checkpoint": CheckpointRecord, "snapshot": SnapshotManifest,
             "collection": CollectionManifest, "collection-statistics": CollectionStatistics}
PAYLOADS = {"discovery_batches": DiscoveryBatchCheckpoint, "references": ReferencesCheckpoint,
            "arxiv_dates": ArxivDateCheckpoint, "rate_limits": RateLimitCheckpoint}
NAME = re.compile(r"[A-Za-z0-9_-]+")
COMMIT = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
# Schema spec section 11: each rate = numerator / denominator, null when the denominator is 0.
SUMMARY_RATES = {
    "paper_success_rate": ("papers_complete", "papers_discovered"),
    "metadata_success_rate": ("metadata_complete_papers", "papers_discovered"),
    "source_success_rate": ("source_versions_success", "source_versions_expected"),
    "references_query_success_rate": ("references_fully_fetched_papers", "papers_discovered"),
    "reference_metadata_success_rate": ("references_complete_total", "references_arxiv_total"),
    "mean_arxiv_references_per_paper": ("references_arxiv_total", "references_fully_fetched_papers"),
}


def validate_pipeline_document(document, kind):
    """Structure first, then cross-field rules; an unknown kind is a programming error."""
    if kind not in DOCUMENTS:
        raise ValueError(f"Unknown pipeline document kind: {kind!r}")
    if not isinstance(document, dict):
        return ["Document must be an object"]
    try:
        if json.loads(canonical(document)) != document:
            raise ValueError()
    except (TypeError, ValueError):
        return ["Document must contain finite JSON values only"]
    paths = []
    if kind == "checkpoint" and document.get("namespace") in PAYLOADS:
        # The payload union would accept another namespace's payload, so check the exact one.
        paths += _check(document.get("payload"), PAYLOADS[document["namespace"]], "/payload")
    paths += _check(document, DOCUMENTS[kind], "")
    if paths:
        return [f"{p or '/'}: wrong type or missing/unrecognized field" for p in sorted(set(paths))]
    checks = {"checkpoint": _checkpoint, "snapshot": _snapshot,
              "collection": _collection, "collection-statistics": _statistics}
    return checks[kind](document)


def _is_utc(value):
    try:
        utc(value)
        return True
    except (ValueError, AttributeError):
        return False


def _relative_path(value):
    """Portable POSIX path inside the bundle: no absolute, drive, '..' or backslash."""
    parts = value.split("/")
    return ("\\" not in value and "\x00" not in value and not value.startswith("/")
            and ":" not in parts[0] and all(p not in ("", ".", "..") for p in parts))


def _key_parts(key):
    try:
        parts = json.loads(key)
    except ValueError:
        return None
    return parts if isinstance(parts, list) and parts and canonical(parts) == key else None


def _checkpoint(record):
    errors = []
    payload, namespace = record["payload"], record["namespace"]
    if not _is_utc(record["updated_at"]):
        errors.append("/updated_at: expected a UTC timestamp ending in Z")
    parts = _key_parts(record["key"])
    if parts is None:
        return errors + ["/key: must be a nonempty canonical JSON array"]
    if namespace == "discovery_batches":
        expected = [payload["owner"], payload["range_config"], payload["max_candidates"],
                    payload["batch_size"], payload["offset"]]
        if parts != expected:
            errors.append("/key: does not match owner/range/limits/batch/offset in payload")
        if payload["range_config"]["owner"] != payload["owner"]:
            errors.append("/payload/range_config/owner: differs from payload owner")
        if payload["batch_size"] < 1 or payload["offset"] < 0 or (
                payload["max_candidates"] is not None and payload["max_candidates"] < 0):
            errors.append("/payload: batch_size must be >= 1, offset and max_candidates >= 0")
        candidates = payload["candidate_ids"]
        if len(candidates) > payload["batch_size"] or len(set(candidates)) != len(candidates):
            errors.append("/payload/candidate_ids: duplicated or larger than batch_size")
        if not all(is_valid_arxiv_id(i) for i in candidates):
            errors.append("/payload/candidate_ids: invalid arXiv ID")
        groups = payload["found_ids"] + payload["excluded_ids"] + payload["unresolved_ids"]
        if len(groups) != len(set(groups)) or set(groups) != set(candidates):
            errors.append("/payload: found/excluded/unresolved must partition candidate_ids")
        if bool(payload["unresolved_ids"]) != (payload["error"] is not None):
            errors.append("/payload/error: required exactly when unresolved_ids is nonempty")
    elif namespace == "references":
        if parts[:2] != [payload["owner"], payload["arxiv_id"]]:
            errors.append("/key: must start with [owner, arxiv_id] of the payload")
        if not is_valid_arxiv_id(payload["arxiv_id"]):
            errors.append("/payload/arxiv_id: invalid arXiv ID")
        if payload["page_size"] < 1 or not payload["fields"].strip():
            errors.append("/payload: page_size must be >= 1 and fields nonempty")
        expected_offset = 0
        for i, page in enumerate(payload["pages"]):
            at = f"/payload/pages/{i}"
            if page["offset"] != expected_offset:
                errors.append(f"{at}/offset: pages must be contiguous from 0")
            if len(page["raw_items"]) > payload["page_size"]:
                errors.append(f"{at}/raw_items: more items than page_size")
            last = i == len(payload["pages"]) - 1
            if page["next_offset"] is None and not last:
                errors.append(f"{at}/next_offset: only the last page may end pagination")
            if page["next_offset"] is not None and page["next_offset"] <= page["offset"]:
                errors.append(f"{at}/next_offset: must be greater than offset")
            if not _is_utc(page["fetched_at"]):
                errors.append(f"{at}/fetched_at: expected a UTC timestamp ending in Z")
            expected_offset = page["next_offset"]
    elif namespace == "arxiv_dates":
        if parts != [payload["arxiv_id"]]:
            errors.append("/key: must be [arxiv_id] of the payload")
        if not is_valid_arxiv_id(payload["arxiv_id"]):
            errors.append("/payload/arxiv_id: invalid arXiv ID")
        if not valid_iso_date(payload["submission_date"]):
            errors.append("/payload/submission_date: invalid ISO date")
        if not payload["source"].strip():
            errors.append("/payload/source: must name where the date came from")
        if not _is_utc(payload["fetched_at"]):
            errors.append("/payload/fetched_at: expected a UTC timestamp ending in Z")
    else:
        if parts != [payload["group"]]:
            errors.append("/key: must be [group] of the payload")
        times = [payload["next_allowed_at"], *payload["recent_requests_at"]]
        if not all(_is_utc(t) for t in times):
            errors.append("/payload: request times must be UTC timestamps ending in Z")
        elif [utc(t) for t in payload["recent_requests_at"]] != sorted(utc(t) for t in payload["recent_requests_at"]):
            errors.append("/payload/recent_requests_at: must be in ascending order")
        if payload["consecutive_429"] < 0:
            errors.append("/payload/consecutive_429: must be >= 0")
    return errors


def _snapshot(manifest):
    errors = []
    for field in ("snapshot_id", "run_id"):
        if not NAME.fullmatch(manifest[field]):
            errors.append(f"/{field}: only letters, digits, '_' and '-' are allowed")
    if not _is_utc(manifest["created_at"]):
        errors.append("/created_at: expected a UTC timestamp ending in Z")
    owners = manifest["owners"]
    if not owners or len(set(owners)) != len(owners):
        errors.append("/owners: must be nonempty without duplicates")
    commits = manifest["producer_commits"]
    if not COMMIT.fullmatch(manifest["exporter_commit"]):
        errors.append("/exporter_commit: expected a 40-character lowercase git hash")
    if not all(COMMIT.fullmatch(c) for c in commits) or len(set(commits)) != len(commits):
        errors.append("/producer_commits: expected unique 40-character git hashes")
    if manifest["unknown_provenance_tasks"] < 0:
        errors.append("/unknown_provenance_tasks: must be >= 0")
    if not SHA256.fullmatch(manifest["config_fingerprint"]):
        errors.append("/config_fingerprint: expected a 64-character lowercase hex digest")
    if not manifest["origin_output_root"].strip():
        errors.append("/origin_output_root: must not be empty")
    paths = [f["path"] for f in manifest["files"]]
    if not paths:
        errors.append("/files: a snapshot must list its files")
    if len(set(paths)) != len(paths):
        errors.append("/files: duplicated path")
    for i, f in enumerate(manifest["files"]):
        if not _relative_path(f["path"]):
            errors.append(f"/files/{i}/path: unsafe or non-relative path")
        if f["size_bytes"] < 0:
            errors.append(f"/files/{i}/size_bytes: must be >= 0")
        if not SHA256.fullmatch(f["sha256"]):
            errors.append(f"/files/{i}/sha256: expected a 64-character lowercase hex digest")
    return errors


def _collection(manifest):
    errors = []
    if not NAME.fullmatch(manifest["collection_id"]):
        errors.append("/collection_id: only letters, digits, '_' and '-' are allowed")
    if not _is_utc(manifest["created_at"]):
        errors.append("/created_at: expected a UTC timestamp ending in Z")
    members = manifest["members"]
    if not members:
        errors.append("/members: a collection needs at least one snapshot")
    errors += _member_errors(members, "/members")
    return errors


def _member_errors(members, at):
    errors, seen_snapshots, seen_owners = [], set(), set()
    for i, m in enumerate(members):
        if not NAME.fullmatch(m["snapshot_id"]) or not NAME.fullmatch(m["run_id"]):
            errors.append(f"{at}/{i}: invalid snapshot_id or run_id")
        if m["snapshot_id"] in seen_snapshots:
            errors.append(f"{at}/{i}/snapshot_id: snapshot listed twice")
        seen_snapshots.add(m["snapshot_id"])
        if not m["owners"] or len(set(m["owners"])) != len(m["owners"]):
            errors.append(f"{at}/{i}/owners: must be nonempty without duplicates")
        # One chosen handoff per owner: two members may not both claim an owner.
        if seen_owners & set(m["owners"]):
            errors.append(f"{at}/{i}/owners: owner already provided by another snapshot")
        seen_owners |= set(m["owners"])
        if not COMMIT.fullmatch(m["exporter_commit"]):
            errors.append(f"{at}/{i}/exporter_commit: expected a 40-character lowercase git hash")
        for field in ("root", "manifest_path"):
            if not _relative_path(m[field]):
                errors.append(f"{at}/{i}/{field}: unsafe or non-relative path")
    return errors


def _summary_errors(summary, at):
    errors = []
    counts = [k for k, h in get_type_hints(Summary).items() if h is int]
    if any(summary[k] < 0 for k in counts):
        errors.append(f"{at}: counts must be >= 0")
    if (summary["source_versions_success"] + summary["source_versions_unsupported"]
            + summary["source_versions_unresolved"] != summary["source_versions_expected"]):
        errors.append(f"{at}: success + unsupported + unresolved must equal source_versions_expected")
    for name, (numerator, denominator) in SUMMARY_RATES.items():
        value, den = summary[name], summary[denominator]
        if (value is None) != (den == 0) or (value is not None and abs(value - summary[numerator] / den) > 1e-9):
            errors.append(f"{at}/{name}: must equal {numerator} / {denominator} (null when it is 0)")
        elif name.endswith("_rate") and value is not None and not 0 <= value <= 1:
            errors.append(f"{at}/{name}: rate must be between 0 and 1")
    for name in ("mean_extracted_bytes_before_filter", "mean_retained_source_bytes"):
        if (summary[name] is None) != (summary["source_size_sample_count"] == 0) or (
                summary[name] is not None and summary[name] < 0):
            errors.append(f"{at}/{name}: null exactly when source_size_sample_count is 0, else >= 0")
    return errors


def _statistics(stats):
    errors = []
    if not NAME.fullmatch(stats["collection_id"]):
        errors.append("/collection_id: only letters, digits, '_' and '-' are allowed")
    errors += _member_errors(stats["sources"], "/sources")
    present = {o for m in stats["sources"] for o in m["owners"]}
    missing = stats["missing_owners"]
    if len(set(missing)) != len(missing) or present & set(missing):
        errors.append("/missing_owners: duplicated or also present in sources")
    scope = stats["scope"]
    tasks = [(p["owner"], p["arxiv_id"]) for p in scope["paper_tasks"]]
    with_papers = {o for o, _ in tasks}
    if (len(set(tasks)) != len(tasks) or scope["paper_count"] != len(tasks)
            or scope["owner_count"] != len(with_papers) or not with_papers <= present):
        errors.append("/scope: paper_tasks duplicated, counts not derived from them, or owner outside sources")
    # Like per-run statistics, by_owner holds exactly the owners that appear in paper_tasks.
    if set(stats["by_owner"]) != with_papers:
        errors.append("/by_owner: owners must equal the owners in scope.paper_tasks")
    for owner, summary in [("", stats["summary"]), *stats["by_owner"].items()]:
        at = f"/by_owner/{owner}" if owner else "/summary"
        errors += _summary_errors(summary, at)
        expected = len(tasks) if not owner else sum(o == owner for o, _ in tasks)
        if summary["papers_discovered"] != expected:
            errors.append(f"{at}/papers_discovered: must equal the papers in scope.paper_tasks")
    # Group rates come from summed counts, so every count must add up across owners.
    for k in [k for k, h in get_type_hints(Summary).items() if h is int]:
        if stats["summary"][k] != sum(s[k] for s in stats["by_owner"].values()):
            errors.append(f"/summary/{k}: differs from the sum over by_owner")
    return errors
