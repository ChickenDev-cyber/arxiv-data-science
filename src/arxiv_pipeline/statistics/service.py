"""T6 Statistics Service: per-run and aggregated statistics conforming to Schema 1.2 and CollectionStatistics."""

from __future__ import annotations

import json
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Generator

from ..contracts import (
    Owner,
    PaperTask,
    Result,
    RunScope,
    Scope,
    Statistics,
    Summary,
    SCHEMA_VERSION,
)
from ..pipeline_contracts import (
    CollectionMember,
    CollectionStatistics,
    RunReader,
    VerifiedRun,
)
from ..validation.corpus import paper_issues, validate_corpus, validate_handoff


def _safe_div(numerator: int | float, denominator: int | float) -> float | None:
    """Safe division returning None if denominator is zero or invalid."""
    if not denominator or denominator <= 0:
        return None
    return float(numerator) / float(denominator)


OWNER_ALIAS_MAP: dict[str, Owner] = {
    "trong": "trong",
    "huynhnhi": "huynh-nhi",
    "huynh-nhi": "huynh-nhi",
    "khang": "khang",
    "mannhi": "man-nhi",
    "man-nhi": "man-nhi",
    "hoang": "hoang",
    "huy-hoang": "hoang",
}

ALL_CANONICAL_OWNERS: list[Owner] = ["trong", "huynh-nhi", "khang", "man-nhi", "hoang"]


def normalize_owner(raw_owner: Any) -> Owner:
    if not isinstance(raw_owner, str):
        return "hoang"
    key = raw_owner.strip().lower().replace("_", "-")
    compact = key.replace("-", "")
    return OWNER_ALIAS_MAP.get(compact, OWNER_ALIAS_MAP.get(key, key))  # type: ignore


def normalize_result(raw: dict[str, Any]) -> Result:
    """Normalize legacy or partial results to conform to Schema 1.2 Result structure."""
    r = deepcopy(raw)

    if "step" not in r and "task_type" in r:
        r["step"] = r["task_type"]
    if "step" not in r:
        r["step"] = "discovery"

    r["owner"] = normalize_owner(r.get("owner"))

    if "version" not in r:
        r["version"] = None

    if "status" not in r:
        r["status"] = "success"

    data = r.get("data")
    if data is None or not isinstance(data, dict):
        data = {}
        for k in (
            "versions", "versions_complete", "title", "authors", "submission_date",
            "source_kind", "source_dir", "files", "references", "raw_references", "complete"
        ):
            if k in r:
                data[k] = r[k]
        r["data"] = data

    metrics = r.get("metrics")
    if metrics is None or not isinstance(metrics, dict):
        metrics = {}
        for k in (
            "download_bytes", "extracted_bytes_before_filter", "retained_source_bytes",
            "removed_figure_bytes", "removed_other_bytes", "s2_reference_count",
            "arxiv_reference_count", "complete_reference_count"
        ):
            if k in r:
                metrics[k] = r[k]
        r["metrics"] = metrics

    if "missing_fields" not in r:
        r["missing_fields"] = []
    if "error" not in r:
        r["error"] = None
    if "provenance" not in r:
        r["provenance"] = {}

    return r  # type: ignore


def compute_summary(tasks: list[PaperTask], results: list[Result]) -> Summary:
    """Compute Summary metrics for a given subset of paper tasks and accepted results.
    
    Adheres strictly to the 20 fields of Schema 1.2 Summary contract.
    No extra timing fields are injected here.
    """
    papers_discovered = len(tasks)
    norm_results = [normalize_result(r) for r in results]

    # Keyed lookup for fast querying: (owner, step, arxiv_id, version) -> Result
    bykey: dict[tuple[str, str, str | None, int | None], Result] = {
        (r["owner"], r["step"], r.get("arxiv_id"), r.get("version")): r for r in norm_results
    }

    # 1. Paper and Metadata Completeness
    metadata_complete_papers = 0
    papers_complete = 0

    for task in tasks:
        owner, aid = task["owner"], task["arxiv_id"]
        meta = bykey.get((owner, "metadata", aid, None))
        if meta and meta.get("status") == "success":
            metadata_complete_papers += 1

        issues = paper_issues(task, bykey)
        if not issues:
            papers_complete += 1

    paper_success_rate = _safe_div(papers_complete, papers_discovered)
    metadata_success_rate = _safe_div(metadata_complete_papers, papers_discovered)

    # 2. Source Versions Metrics
    source_results = [r for r in results if r["step"] == "sources"]
    metadata_results = [r for r in results if r["step"] == "metadata"]

    source_versions_expected = 0
    for m in metadata_results:
        versions = (m.get("data") or {}).get("versions") or []
        source_versions_expected += len(versions)

    if source_versions_expected == 0 and source_results:
        source_versions_expected = len(source_results)

    source_versions_success = sum(1 for r in source_results if r.get("status") == "success")
    source_versions_unsupported = sum(1 for r in source_results if r.get("status") == "unsupported")
    source_versions_unresolved = max(
        0, source_versions_expected - source_versions_success - source_versions_unsupported
    )
    source_success_rate = _safe_div(source_versions_success, source_versions_expected)

    # Source byte metrics:
    # Dung lượng chỉ tính source success có đủ cặp số đo trước/sau (non-negative integers)
    extracted_bytes_list: list[int] = []
    retained_bytes_list: list[int] = []

    for r in source_results:
        if r.get("status") != "success":
            continue
        metrics = r.get("metrics") or {}
        extracted = metrics.get("extracted_bytes_before_filter")
        retained = metrics.get("retained_source_bytes")

        # Must have both measurements as valid non-negative numbers
        if (
            isinstance(extracted, (int, float)) and not isinstance(extracted, bool) and extracted >= 0
            and isinstance(retained, (int, float)) and not isinstance(retained, bool) and retained >= 0
        ):
            extracted_bytes_list.append(int(extracted))
            retained_bytes_list.append(int(retained))

    source_size_sample_count = len(retained_bytes_list)
    mean_extracted_bytes_before_filter = (
        _safe_div(sum(extracted_bytes_list), source_size_sample_count)
        if source_size_sample_count > 0 else None
    )
    mean_retained_source_bytes = (
        _safe_div(sum(retained_bytes_list), source_size_sample_count)
        if source_size_sample_count > 0 else None
    )

    # 3. References Metrics
    reference_results = [r for r in results if r["step"] == "references"]

    # Fully fetched papers: must have complete == True in data
    fully_fetched_refs = [
        r for r in reference_results
        if r.get("data") and r["data"].get("complete") is True
    ]
    references_fully_fetched_papers = len(fully_fetched_refs)
    references_query_success_rate = _safe_div(references_fully_fetched_papers, papers_discovered)

    references_metadata_complete_papers = sum(
        1 for r in reference_results if r.get("status") in ("success", "empty")
    )

    # Loại references chưa hết trang khỏi tổng:
    # Chỉ tính references_arxiv_total và references_complete_total cho những bài có paging hoàn chỉnh (complete == True)
    arxiv_ref_counts: list[int] = []
    complete_ref_counts: list[int] = []

    for r in fully_fetched_refs:
        metrics = r.get("metrics") or {}
        arxiv_cnt = metrics.get("arxiv_reference_count")
        comp_cnt = metrics.get("complete_reference_count")

        if isinstance(arxiv_cnt, (int, float)) and not isinstance(arxiv_cnt, bool) and arxiv_cnt >= 0:
            arxiv_ref_counts.append(int(arxiv_cnt))
        elif r.get("data") and "references" in r["data"] and isinstance(r["data"]["references"], dict):
            arxiv_ref_counts.append(len(r["data"]["references"]))

        if isinstance(comp_cnt, (int, float)) and not isinstance(comp_cnt, bool) and comp_cnt >= 0:
            complete_ref_counts.append(int(comp_cnt))

    references_arxiv_total = sum(arxiv_ref_counts)
    references_complete_total = sum(complete_ref_counts)
    reference_metadata_success_rate = _safe_div(references_complete_total, references_arxiv_total)
    mean_arxiv_references_per_paper = _safe_div(references_arxiv_total, references_fully_fetched_papers)

    # Return exactly the 20 Schema 1.2 Summary fields
    summary_dict: Summary = {
        "papers_discovered": papers_discovered,
        "metadata_complete_papers": metadata_complete_papers,
        "papers_complete": papers_complete,
        "paper_success_rate": paper_success_rate,
        "source_versions_expected": source_versions_expected,
        "source_versions_success": source_versions_success,
        "source_versions_unsupported": source_versions_unsupported,
        "source_versions_unresolved": source_versions_unresolved,
        "references_fully_fetched_papers": references_fully_fetched_papers,
        "references_query_success_rate": references_query_success_rate,
        "references_metadata_complete_papers": references_metadata_complete_papers,
        "metadata_success_rate": metadata_success_rate,
        "source_success_rate": source_success_rate,
        "references_arxiv_total": references_arxiv_total,
        "references_complete_total": references_complete_total,
        "reference_metadata_success_rate": reference_metadata_success_rate,
        "mean_arxiv_references_per_paper": mean_arxiv_references_per_paper,
        "source_size_sample_count": source_size_sample_count,
        "mean_extracted_bytes_before_filter": mean_extracted_bytes_before_filter,
        "mean_retained_source_bytes": mean_retained_source_bytes,
    }

    return summary_dict


class DefaultRunReader:
    """Production implementation of RunReader protocol.
    
    Verifies corpus, ensures validation.json is present and valid,
    and rejects duplicate accepted results.
    """

    @contextmanager
    def open(self, output_root: Path, run_id: str) -> Generator[VerifiedRun, None, None]:
        output_root = Path(output_root)
        run_dir = output_root / "runs" / run_id
        if not run_dir.exists():
            if (output_root / "scope.json").exists() and (run_id == "standalone" or run_id in str(output_root)):
                run_dir = output_root
            else:
                raise FileNotFoundError(f"Run directory not found: {run_dir}")

        val_file = run_dir / "validation.json"
        if not val_file.exists():
            raise ValueError(f"Run {run_id} rejected: missing validation.json")
        try:
            val_data = json.loads(val_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            raise ValueError(f"Run {run_id} rejected: corrupted validation.json: {e}") from e

        if not isinstance(val_data, dict) or not val_data.get("valid", False):
            raise ValueError(f"Run {run_id} rejected: validation failed: {val_data.get('issues') if isinstance(val_data, dict) else 'invalid structure'}")

        scope_file = run_dir / "scope.json"
        if not scope_file.exists():
            raise FileNotFoundError(f"scope.json not found in {run_dir}")
        try:
            scope_raw = json.loads(scope_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            raise ValueError(f"Run {run_id} rejected: corrupted scope.json: {e}") from e

        if "scope" not in scope_raw:
            run_scope: RunScope = {
                "schema_version": scope_raw.get("schema_version", SCHEMA_VERSION),
                "run_id": run_id,
                "is_mock": scope_raw.get("is_mock", True),
                "scope": scope_raw,
            }
        else:
            run_scope = scope_raw

        results_file = run_dir / "accepted_results.jsonl"
        if not results_file.exists():
            raise FileNotFoundError(f"accepted_results.jsonl not found in {run_dir}")

        results: list[Result] = []
        seen_keys: set[tuple[str, str, str | None, int | None]] = set()

        with open(results_file, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    r = json.loads(line_str)
                except json.JSONDecodeError as e:
                    raise ValueError(f"Corrupted result line {line_no} in {results_file}: {e}") from e

                key = (r.get("owner"), r.get("step"), r.get("arxiv_id"), r.get("version"))
                if key in seen_keys:
                    raise ValueError(f"Run {run_id} rejected: duplicate accepted result for {key}")
                seen_keys.add(key)
                results.append(r)

        # Check handoff consistency using canonical handoff validator
        handoff_errors = validate_handoff(run_scope, results)
        if handoff_errors:
            raise ValueError(
                f"Run {run_id} handoff validation failed with {len(handoff_errors)} issues: {handoff_errors}"
            )

        # Compute corpus quality
        corpus_issues = validate_corpus(run_scope, results)
        quality_file = run_dir / "quality.json"
        quality_data: dict[str, Any] = {"valid": not bool(corpus_issues), "issues": corpus_issues}
        if quality_file.exists():
            try:
                loaded_q = json.loads(quality_file.read_text(encoding="utf-8"))
                if isinstance(loaded_q, dict):
                    quality_data = loaded_q
            except Exception:
                pass

        verified_run = VerifiedRun(
            output_root=run_dir,
            scope=run_scope,
            accepted_results=results,
            validation=val_data,
            quality=quality_data,  # type: ignore
            files=[],
            run_state="finished",
            config_fingerprint="",
            recorded_output_root=str(output_root),
        )
        yield verified_run


class StatisticsService:
    """Service to compute, aggregate and persist Schema 1.2 statistics and CollectionStatistics."""

    def __init__(self, run_reader: RunReader | None = None, check_validation: bool = True):
        self.check_validation = check_validation
        if run_reader is not None:
            self.run_reader: RunReader | None = run_reader
        elif check_validation:
            self.run_reader = DefaultRunReader()
        else:
            self.run_reader = None

    def load_run_data(self, output_root: Path, run_id: str) -> tuple[RunScope, list[Result]]:
        """Load and verify run data via RunReader or direct fallback."""
        if self.run_reader is not None:
            with self.run_reader.open(output_root, run_id) as v_run:
                return v_run.scope, v_run.accepted_results

        # Fallback when check_validation=False
        output_root = Path(output_root)
        run_dir = output_root / "runs" / run_id
        if not run_dir.exists():
            if (output_root / "scope.json").exists():
                run_dir = output_root
            else:
                raise FileNotFoundError(f"Run directory not found: {run_dir}")

        scope_file = run_dir / "scope.json"
        results_file = run_dir / "accepted_results.jsonl"

        if not scope_file.exists():
            raise FileNotFoundError(f"scope.json not found in {run_dir}")
        if not results_file.exists():
            raise FileNotFoundError(f"accepted_results.jsonl not found in {run_dir}")

        raw_scope = json.loads(scope_file.read_text(encoding="utf-8"))
        if "scope" not in raw_scope:
            scope_data: RunScope = {
                "schema_version": raw_scope.get("schema_version", SCHEMA_VERSION),
                "run_id": run_id,
                "is_mock": raw_scope.get("is_mock", True),
                "scope": raw_scope,
            }
        else:
            scope_data = raw_scope

        results: list[Result] = []
        with open(results_file, "r", encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if line_str:
                    results.append(json.loads(line_str))

        return scope_data, results

    def build_statistics(self, output_root: Path, run_id: str) -> Statistics:
        """Compute complete Schema 1.2 Statistics for a single run."""
        scope_data, results = self.load_run_data(output_root, run_id)

        raw_scope = scope_data["scope"]
        raw_tasks = raw_scope.get("paper_tasks", [])
        paper_tasks: list[PaperTask] = []
        for t in raw_tasks:
            paper_tasks.append({
                "owner": normalize_owner(t.get("owner")),
                "arxiv_id": t.get("arxiv_id", ""),
            })

        norm_results = [normalize_result(r) for r in results]

        # Overall summary
        summary = compute_summary(paper_tasks, norm_results)

        # Per-owner breakdown
        by_owner: dict[Owner, Summary] = {}
        all_owners: set[Owner] = {t["owner"] for t in paper_tasks}
        for r_cfg in raw_scope.get("ranges", []):
            if "owner" in r_cfg:
                all_owners.add(normalize_owner(r_cfg["owner"]))
        for r in norm_results:
            all_owners.add(r["owner"])

        for owner in sorted(all_owners):
            owner_tasks = [t for t in paper_tasks if t["owner"] == owner]
            owner_results = [r for r in norm_results if r["owner"] == owner]
            by_owner[owner] = compute_summary(owner_tasks, owner_results)

        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "is_mock": scope_data.get("is_mock", True),
            "scope": raw_scope,
            "summary": summary,
            "by_owner": by_owner,
        }

    def aggregate_statistics(
        self,
        stats_list: list[Statistics],
        collection_id: str = "team-collection",
        sources: list[CollectionMember] | None = None,
        aggregated_run_id: str | None = None,
    ) -> CollectionStatistics:
        """Aggregate multiple single-run statistics across team members into a unified CollectionStatistics.
        
        Rejects mixing mock and real runs.
        Rejects duplicate owners across runs.
        Outputs exact CollectionStatistics conforming to pipeline contracts.
        """
        if not stats_list:
            raise ValueError("stats_list cannot be empty for aggregation")

        cid = aggregated_run_id or collection_id

        # 1. Reject mixing mock and real runs
        mock_flags = {bool(s.get("is_mock", True)) for s in stats_list}
        if len(mock_flags) > 1:
            raise ValueError("Cannot aggregate mixed mock and real runs")
        is_mock = next(iter(mock_flags))

        # 2. Reject duplicate owners across runs
        seen_owners: set[Owner] = set()
        for s in stats_list:
            run_owners = set(s.get("by_owner", {}).keys())
            overlap = seen_owners.intersection(run_owners)
            if overlap:
                raise ValueError(f"Duplicate owner(s) detected across aggregated runs: {sorted(overlap)}")
            seen_owners.update(run_owners)

        # 3. Missing owners
        missing_owners = [o for o in ALL_CANONICAL_OWNERS if o not in seen_owners]

        # Combine scope paper tasks and ranges
        combined_tasks: list[PaperTask] = []
        combined_ranges: list[Any] = []
        seen_ranges: set[str] = set()
        seen_tasks: set[tuple[str, str]] = set()

        by_owner: dict[Owner, Summary] = {}

        for s in stats_list:
            sc = s.get("scope", {})
            for r in sc.get("ranges", []):
                r_key = f"{r.get('owner')}_{r.get('start_month')}_{r.get('start_id')}"
                if r_key not in seen_ranges:
                    seen_ranges.add(r_key)
                    combined_ranges.append(r)

            for t in sc.get("paper_tasks", []):
                t_key = (t["owner"], t["arxiv_id"])
                if t_key not in seen_tasks:
                    seen_tasks.add(t_key)
                    combined_tasks.append(t)

            for owner, owner_summary in s.get("by_owner", {}).items():
                by_owner[owner] = deepcopy(owner_summary)

        # Aggregate overall summary by summing raw counts across all owners
        total_discovered = sum(s["papers_discovered"] for s in by_owner.values())
        total_meta_complete = sum(s["metadata_complete_papers"] for s in by_owner.values())
        total_complete = sum(s["papers_complete"] for s in by_owner.values())

        total_src_expected = sum(s["source_versions_expected"] for s in by_owner.values())
        total_src_success = sum(s["source_versions_success"] for s in by_owner.values())
        total_src_unsupported = sum(s["source_versions_unsupported"] for s in by_owner.values())
        total_src_unresolved = sum(s["source_versions_unresolved"] for s in by_owner.values())

        total_ref_fully_fetched = sum(s["references_fully_fetched_papers"] for s in by_owner.values())
        total_ref_meta_complete = sum(s["references_metadata_complete_papers"] for s in by_owner.values())
        total_ref_arxiv = sum(s["references_arxiv_total"] for s in by_owner.values())
        total_ref_complete = sum(s["references_complete_total"] for s in by_owner.values())

        # Weighted byte means using source_size_sample_count as weight
        total_size_samples = sum(s["source_size_sample_count"] for s in by_owner.values())
        weighted_extracted_sum = sum(
            (s["mean_extracted_bytes_before_filter"] or 0.0) * s["source_size_sample_count"]
            for s in by_owner.values()
            if s["mean_extracted_bytes_before_filter"] is not None
        )
        weighted_retained_sum = sum(
            (s["mean_retained_source_bytes"] or 0.0) * s["source_size_sample_count"]
            for s in by_owner.values()
            if s["mean_retained_source_bytes"] is not None
        )

        overall_summary: Summary = {
            "papers_discovered": total_discovered,
            "metadata_complete_papers": total_meta_complete,
            "papers_complete": total_complete,
            "paper_success_rate": _safe_div(total_complete, total_discovered),
            "source_versions_expected": total_src_expected,
            "source_versions_success": total_src_success,
            "source_versions_unsupported": total_src_unsupported,
            "source_versions_unresolved": total_src_unresolved,
            "references_fully_fetched_papers": total_ref_fully_fetched,
            "references_query_success_rate": _safe_div(total_ref_fully_fetched, total_discovered),
            "references_metadata_complete_papers": total_ref_meta_complete,
            "metadata_success_rate": _safe_div(total_meta_complete, total_discovered),
            "source_success_rate": _safe_div(total_src_success, total_src_expected),
            "references_arxiv_total": total_ref_arxiv,
            "references_complete_total": total_ref_complete,
            "reference_metadata_success_rate": _safe_div(total_ref_complete, total_ref_arxiv),
            "mean_arxiv_references_per_paper": _safe_div(total_ref_arxiv, total_ref_fully_fetched),
            "source_size_sample_count": total_size_samples,
            "mean_extracted_bytes_before_filter": _safe_div(weighted_extracted_sum, total_size_samples),
            "mean_retained_source_bytes": _safe_div(weighted_retained_sum, total_size_samples),
        }

        aggregated_scope: Scope = {
            "ranges": combined_ranges,
            "paper_tasks": combined_tasks,
            "discovery_complete": all(s.get("scope", {}).get("discovery_complete", False) for s in stats_list),
            "versions_complete": all(s.get("scope", {}).get("versions_complete", False) for s in stats_list),
            "owner_count": len(seen_owners),
            "paper_count": len(combined_tasks),
        }

        col_stats: CollectionStatistics = {
            "format_version": 1,
            "kind": "arxiv-collection-statistics",
            "schema_version": "1.2",
            "collection_id": cid,
            "is_mock": is_mock,
            "sources": sources or [],
            "scope": aggregated_scope,
            "missing_owners": missing_owners,
            "summary": overall_summary,
            "by_owner": by_owner,
        }
        # Keep run_id for backward compatibility
        col_stats["run_id"] = cid  # type: ignore

        return col_stats

    def save_statistics(self, stats: Statistics | CollectionStatistics, output_file: Path) -> Path:
        """Persist Statistics or CollectionStatistics to a formatted JSON file."""
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        return output_file
