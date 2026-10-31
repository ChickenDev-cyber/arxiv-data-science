"""T6 Statistics Service: per-run and aggregated statistics conforming to Schema 1.2 and CollectionStatistics."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from ..config import ASSIGNED_RANGES, OWNERS
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
from ..collection.inspector import RunInspector
from ..collection.statistics import (
    CollectionStatisticsBuilder,
    OwnerSummarizer,
    combine_summaries,
)
from ..pipeline_contracts import (
    CollectionMember,
    CollectionStatistics,
    RunReader,
    VerifiedCollection,
    VerifiedRun,
)
from ..runner.writer import digest
from ..validation.corpus import paper_issues, validate_corpus, validate_handoff
from ..validation.pipeline_contracts import validate_pipeline_document

ASSIGNED = {c["owner"]: c for c in ASSIGNED_RANGES}


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


class StatisticsService(OwnerSummarizer):
    """Service to compute, aggregate and persist Schema 1.2 statistics and CollectionStatistics."""

    def __init__(self, run_reader: RunReader | None = None, check_validation: bool = True):
        self.check_validation = check_validation
        if run_reader is not None:
            self.run_reader: RunReader | None = run_reader
        elif check_validation:
            self.run_reader = RunInspector()
        else:
            self.run_reader = None

    def summarize(self, run: VerifiedRun, owner: Owner) -> Summary:
        """Summarize a single owner's tasks and results from a verified run. Implements OwnerSummarizer."""
        owner_tasks = [t for t in run.scope["scope"]["paper_tasks"] if t["owner"] == owner]
        owner_results = [r for r in run.accepted_results if r["owner"] == owner]
        return compute_summary(owner_tasks, owner_results)

    def load_run_data(self, output_root: Path, run_id: str) -> tuple[RunScope, list[Result]]:
        """Load and verify run data via RunReader or direct fallback.

        Rejects missing or corrupted corpus files, and verifies file hashes.
        """
        if self.run_reader is not None:
            with self.run_reader.open(output_root, run_id) as v_run:
                # Corpus check: verify that all recorded files exist and match sha256
                if v_run.files:
                    for f in v_run.files:
                        p = Path(v_run.output_root) / f["path"]
                        if not p.is_file():
                            raise ValueError(f"Corpus file missing: {f['path']}")
                        if p.stat().st_size != f["size_bytes"] or digest(p) != f["sha256"]:
                            raise ValueError(f"Corpus file hash mismatch: {f['path']}")
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
        """Compute complete Schema 1.2 Statistics for a single run.

        If scope has no papers, by_owner is strictly empty ({}).
        """
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

        # by_owner holds strictly the owners that appear in paper_tasks (empty if no papers)
        by_owner: dict[Owner, Summary] = {}
        task_owners = sorted({t["owner"] for t in paper_tasks})
        for owner in task_owners:
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
        stats_or_collection: list[Statistics] | VerifiedCollection,
        collection_id: str = "team-collection",
        sources: list[CollectionMember] | None = None,
        aggregated_run_id: str | None = None,
    ) -> CollectionStatistics:
        """Aggregate multiple single-run statistics across team members into a unified CollectionStatistics.

        Supports VerifiedCollection directly via CollectionStatisticsBuilder.
        Rejects mixing mock and real runs.
        Rejects duplicate owners across runs.
        Outputs exact CollectionStatistics conforming to pipeline contracts (without fake run_id).
        """
        if isinstance(stats_or_collection, VerifiedCollection):
            return CollectionStatisticsBuilder(self).build(stats_or_collection)

        stats_list = stats_or_collection
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

        # 3. Combine ranges and paper tasks
        combined_ranges: list[Any] = []
        seen_range_owners: set[str] = set()
        for s in stats_list:
            sc = s.get("scope", {})
            for r in sc.get("ranges", []):
                o = r.get("owner")
                if o and o not in seen_range_owners:
                    seen_range_owners.add(o)
                    combined_ranges.append(r)

        combined_tasks: list[PaperTask] = []
        seen_tasks: set[tuple[str, str]] = set()
        for s in stats_list:
            sc = s.get("scope", {})
            for t in sc.get("paper_tasks", []):
                t_key = (t["owner"], t["arxiv_id"])
                if t_key not in seen_tasks:
                    seen_tasks.add(t_key)
                    combined_tasks.append(t)

        task_owners = sorted({t["owner"] for t in combined_tasks})

        # by_owner: only owners appearing in paper_tasks (if no papers, by_owner = {})
        by_owner: dict[Owner, Summary] = {}
        for s in stats_list:
            for owner, owner_summary in s.get("by_owner", {}).items():
                if owner in task_owners:
                    by_owner[owner] = deepcopy(owner_summary)

        # 4. Discovery complete requires all 5 assigned ranges present and finished
        covers_all = sorted({c["owner"] for c in combined_ranges}) == sorted(OWNERS)
        discovery_complete = covers_all and all(s.get("scope", {}).get("discovery_complete", False) for s in stats_list)

        # versions_complete: must be false when there are no papers
        versions_complete = bool(combined_tasks) and all(s.get("scope", {}).get("versions_complete", False) for s in stats_list)

        aggregated_scope: Scope = {
            "ranges": combined_ranges,
            "paper_tasks": combined_tasks,
            "discovery_complete": discovery_complete,
            "versions_complete": versions_complete,
            "owner_count": len(task_owners),
            "paper_count": len(combined_tasks),
        }

        # 5. Combined summary
        if by_owner:
            overall_summary = combine_summaries(list(by_owner.values()))
        else:
            overall_summary = {
                "papers_discovered": 0,
                "metadata_complete_papers": 0,
                "papers_complete": 0,
                "paper_success_rate": None,
                "source_versions_expected": 0,
                "source_versions_success": 0,
                "source_versions_unsupported": 0,
                "source_versions_unresolved": 0,
                "references_fully_fetched_papers": 0,
                "references_query_success_rate": None,
                "references_metadata_complete_papers": 0,
                "metadata_success_rate": None,
                "source_success_rate": None,
                "references_arxiv_total": 0,
                "references_complete_total": 0,
                "reference_metadata_success_rate": None,
                "mean_arxiv_references_per_paper": None,
                "source_size_sample_count": 0,
                "mean_extracted_bytes_before_filter": None,
                "mean_retained_source_bytes": None,
            }

        # 6. Sources and missing owners
        if sources is not None:
            col_sources = sources
            present_owners = {o for m in col_sources for o in m.get("owners", [])}
        else:
            col_sources = []
            present_owners = set()
            for r in combined_ranges:
                o = r["owner"]
                present_owners.add(o)
                col_sources.append({
                    "snapshot_id": f"snap-{o}",
                    "run_id": f"run-{o}",
                    "owners": [o],
                    "exporter_commit": "0" * 40,
                    "root": f"snap-{o}/corpus",
                    "manifest_path": f"snap-{o}/manifest.json",
                })

        missing = [o for o in ASSIGNED if o not in present_owners]

        col_stats: CollectionStatistics = {
            "format_version": 1,
            "kind": "arxiv-collection-statistics",
            "schema_version": SCHEMA_VERSION,
            "collection_id": cid,
            "is_mock": is_mock,
            "sources": col_sources,
            "scope": aggregated_scope,
            "missing_owners": missing,
            "summary": overall_summary,
            "by_owner": by_owner,
        }

        # Check against schema validator
        problems = validate_pipeline_document(col_stats, "collection-statistics")
        if problems:
            raise ValueError("Collection statistics are inconsistent: " + problems[0])

        return col_stats

    def save_statistics(self, stats: Statistics | CollectionStatistics, output_file: Path) -> Path:
        """Persist Statistics or CollectionStatistics to a formatted JSON file."""
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        return output_file
