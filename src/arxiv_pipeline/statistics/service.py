"""T6 Statistics Service: per-run and aggregated statistics conforming to Schema 1.2."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

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
from ..validation.corpus import paper_issues


def _safe_div(numerator: int | float, denominator: int | float) -> float | None:
    """Safe division returning None if denominator is zero or invalid."""
    if not denominator or denominator <= 0:
        return None
    return float(numerator) / float(denominator)


OWNER_ALIAS_MAP = {
    "trong": "trong",
    "huynhnhi": "huynh-nhi",
    "huynh-nhi": "huynh-nhi",
    "khang": "khang",
    "manhnhi": "man-nhi",
    "man-nhi": "man-nhi",
    "hoang": "hoang",
    "huy-hoang": "hoang",
}


def normalize_owner(raw_owner: Any) -> Owner:
    if not isinstance(raw_owner, str):
        return "hoang"
    key = raw_owner.strip().lower().replace("_", "-")
    compact = key.replace("-", "")
    return OWNER_ALIAS_MAP.get(compact, OWNER_ALIAS_MAP.get(key, key))  # type: ignore


def normalize_result(raw: dict[str, Any]) -> Result:
    """Normalize legacy or partial results to conform to Schema 1.2 Result structure."""
    r = deepcopy(raw)
    
    # 1. Step / task_type normalization
    if "step" not in r and "task_type" in r:
        r["step"] = r["task_type"]
    if "step" not in r:
        r["step"] = "discovery"

    # 2. Owner normalization
    r["owner"] = normalize_owner(r.get("owner"))

    # 3. Version normalization
    if "version" not in r:
        r["version"] = None

    # 4. Status normalization
    if "status" not in r:
        r["status"] = "success"

    # 5. Data & Metrics payload normalization
    data = r.get("data")
    if data is None or not isinstance(data, dict):
        data = {}
        for k in ("versions", "versions_complete", "title", "authors", "submission_date",
                  "source_kind", "source_dir", "files", "references", "raw_references", "complete"):
            if k in r:
                data[k] = r[k]
        r["data"] = data

    metrics = r.get("metrics")
    if metrics is None or not isinstance(metrics, dict):
        metrics = {}
        for k in ("download_bytes", "extracted_bytes_before_filter", "retained_source_bytes",
                  "removed_figure_bytes", "removed_other_bytes", "s2_reference_count",
                  "arxiv_reference_count", "complete_reference_count"):
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
    """Compute Summary metrics for a given subset of paper tasks and accepted results."""
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
        
        # Check overall completeness using canonical rules
        issues = paper_issues(task, bykey)
        if not issues:
            papers_complete += 1

    paper_success_rate = _safe_div(papers_complete, papers_discovered)
    metadata_success_rate = _safe_div(metadata_complete_papers, papers_discovered)

    # 2. Source Versions Metrics
    source_results = [r for r in results if r["step"] == "sources"]
    metadata_results = [r for r in results if r["step"] == "metadata"]

    # Calculate expected versions based on accepted metadata
    source_versions_expected = 0
    for m in metadata_results:
        versions = (m.get("data") or {}).get("versions") or []
        source_versions_expected += len(versions)
    
    # Fallback if metadata not present: count distinct source results
    if source_versions_expected == 0 and source_results:
        source_versions_expected = len(source_results)

    source_versions_success = sum(1 for r in source_results if r.get("status") == "success")
    source_versions_unsupported = sum(1 for r in source_results if r.get("status") == "unsupported")
    source_versions_unresolved = max(0, source_versions_expected - source_versions_success - source_versions_unsupported)
    source_success_rate = _safe_div(source_versions_success, source_versions_expected)

    # Source byte metrics
    extracted_bytes_list: list[int] = []
    retained_bytes_list: list[int] = []

    for r in source_results:
        metrics = r.get("metrics") or {}
        extracted = metrics.get("extracted_bytes_before_filter")
        retained = metrics.get("retained_source_bytes")
        if isinstance(extracted, (int, float)):
            extracted_bytes_list.append(int(extracted))
        if isinstance(retained, (int, float)):
            retained_bytes_list.append(int(retained))

    source_size_sample_count = len(retained_bytes_list)
    mean_extracted_bytes_before_filter = (
        _safe_div(sum(extracted_bytes_list), len(extracted_bytes_list)) if extracted_bytes_list else None
    )
    mean_retained_source_bytes = (
        _safe_div(sum(retained_bytes_list), len(retained_bytes_list)) if retained_bytes_list else None
    )

    # 3. References Metrics
    reference_results = [r for r in results if r["step"] == "references"]
    references_fully_fetched_papers = sum(
        1 for r in reference_results
        if r.get("data") and r["data"].get("complete") is True
    )
    references_query_success_rate = _safe_div(references_fully_fetched_papers, papers_discovered)

    references_metadata_complete_papers = sum(
        1 for r in reference_results if r.get("status") in ("success", "empty")
    )

    arxiv_ref_counts: list[int] = []
    complete_ref_counts: list[int] = []

    for r in reference_results:
        metrics = r.get("metrics") or {}
        arxiv_cnt = metrics.get("arxiv_reference_count")
        comp_cnt = metrics.get("complete_reference_count")

        if isinstance(arxiv_cnt, (int, float)):
            arxiv_ref_counts.append(int(arxiv_cnt))
        elif r.get("data") and "references" in r["data"]:
            arxiv_ref_counts.append(len(r["data"]["references"]))

        if isinstance(comp_cnt, (int, float)):
            complete_ref_counts.append(int(comp_cnt))

    references_arxiv_total = sum(arxiv_ref_counts)
    references_complete_total = sum(complete_ref_counts)
    reference_metadata_success_rate = _safe_div(references_complete_total, references_arxiv_total)
    mean_arxiv_references_per_paper = (
        _safe_div(references_arxiv_total, len(reference_results)) if reference_results else None
    )

    # 4. Task and Per-paper Timings (theo yêu cầu đề bài)
    discovery_duration: float = 0.0
    has_discovery_timing = False
    per_paper_durations: dict[str, float] = {}

    for r in norm_results:
        dur = r.get("duration_seconds")
        if isinstance(dur, (int, float)) and dur > 0:
            step = r.get("step")
            if step == "discovery":
                discovery_duration += float(dur)
                has_discovery_timing = True
            elif step in ("metadata", "sources", "references"):
                aid = r.get("arxiv_id")
                if aid:
                    per_paper_durations[aid] = round(per_paper_durations.get(aid, 0.0) + float(dur), 4)

    mean_paper_seconds = (
        round(sum(per_paper_durations.values()) / len(per_paper_durations), 4)
        if per_paper_durations else None
    )
    discovery_seconds = round(discovery_duration, 4) if has_discovery_timing else None

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
    
    # Optional timing extensions
    summary_dict["discovery_seconds"] = discovery_seconds  # type: ignore
    summary_dict["mean_paper_seconds"] = mean_paper_seconds  # type: ignore
    summary_dict["per_paper_seconds"] = per_paper_durations  # type: ignore

    return summary_dict


class StatisticsService:
    """Service to compute, aggregate and persist Schema 1.2 statistics."""

    def __init__(self, check_validation: bool = True):
        self.check_validation = check_validation

    def load_run_data(self, output_root: Path, run_id: str) -> tuple[RunScope, list[Result]]:
        """Load scope.json and accepted_results.jsonl from runner output directory."""
        run_dir = output_root / "runs" / run_id
        if not run_dir.exists():
            # Alternative: check directly under output_root if it's already the run dir
            if (output_root / "scope.json").exists():
                run_dir = output_root
            else:
                raise FileNotFoundError(f"Run directory not found: {run_dir}")

        scope_file = run_dir / "scope.json"
        results_file = run_dir / "accepted_results.jsonl"
        val_file = run_dir / "validation.json"

        if not scope_file.exists():
            raise FileNotFoundError(f"scope.json not found in {run_dir}")
        if not results_file.exists():
            raise FileNotFoundError(f"accepted_results.jsonl not found in {run_dir}")

        if self.check_validation and val_file.exists():
            try:
                val_data = json.loads(val_file.read_text(encoding="utf-8"))
                if not val_data.get("valid", True):
                    raise ValueError(f"Run {run_id} validation status is invalid: {val_data.get('issues')}")
            except (json.JSONDecodeError, OSError):
                pass

        scope_data: RunScope = json.loads(scope_file.read_text(encoding="utf-8"))
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
        
        # Support both wrapped RunScope (scope_data["scope"]) and legacy flat scope formats
        raw_scope: Scope
        if isinstance(scope_data, dict) and "scope" in scope_data:
            raw_scope = scope_data["scope"]
        else:
            raw_scope = scope_data  # type: ignore

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
        # Also include any owners from range configs if present
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
        aggregated_run_id: str = "team-aggregated",
    ) -> Statistics:
        """Aggregate multiple single-run statistics across team members into a single unified report."""
        if not stats_list:
            raise ValueError("stats_list cannot be empty for aggregation")

        is_mock = all(s.get("is_mock", True) for s in stats_list)

        # Combine scope paper tasks and ranges, deduplicating tasks
        combined_tasks: dict[tuple[str, str], PaperTask] = {}
        combined_ranges: list[Any] = []
        seen_ranges: set[str] = set()

        by_owner: dict[Owner, Summary] = {}

        for s in stats_list:
            sc = s.get("scope", {})
            for r in sc.get("ranges", []):
                r_key = f"{r.get('owner')}_{r.get('start_month')}_{r.get('start_id')}"
                if r_key not in seen_ranges:
                    seen_ranges.add(r_key)
                    combined_ranges.append(r)

            for t in sc.get("paper_tasks", []):
                combined_tasks[(t["owner"], t["arxiv_id"])] = t

            # Copy by_owner summaries
            for owner, owner_summary in s.get("by_owner", {}).items():
                by_owner[owner] = deepcopy(owner_summary)

        all_tasks = list(combined_tasks.values())
        unique_owners = {t["owner"] for t in all_tasks}

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

        # Weighted byte means
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
            "mean_arxiv_references_per_paper": _safe_div(total_ref_arxiv, total_discovered),
            "source_size_sample_count": total_size_samples,
            "mean_extracted_bytes_before_filter": _safe_div(weighted_extracted_sum, total_size_samples),
            "mean_retained_source_bytes": _safe_div(weighted_retained_sum, total_size_samples),
        }

        aggregated_scope: Scope = {
            "ranges": combined_ranges,
            "paper_tasks": all_tasks,
            "discovery_complete": all(s.get("scope", {}).get("discovery_complete", False) for s in stats_list),
            "versions_complete": all(s.get("scope", {}).get("versions_complete", False) for s in stats_list),
            "owner_count": len(unique_owners),
            "paper_count": len(all_tasks),
        }

        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": aggregated_run_id,
            "is_mock": is_mock,
            "scope": aggregated_scope,
            "summary": overall_summary,
            "by_owner": by_owner,
        }

    def save_statistics(self, stats: Statistics, output_file: Path) -> Path:
        """Persist Statistics to a formatted JSON file."""
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        return output_file
