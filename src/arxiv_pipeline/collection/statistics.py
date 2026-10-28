"""Group statistics from per-owner summaries: add counts, then recompute rates."""
from typing import Protocol, get_type_hints

from ..config import ASSIGNED_RANGES, OWNERS
from ..contracts import Owner, Scope, SCHEMA_VERSION, Summary
from ..pipeline_contracts import CollectionStatistics, VerifiedCollection, VerifiedRun
from ..validation.pipeline_contracts import SUMMARY_RATES, validate_pipeline_document

COUNTS = [k for k, hint in get_type_hints(Summary).items() if hint is int]
# Derived value = numerator / denominator over summed counts, never an average of rates.
RATES = SUMMARY_RATES
# Means over source samples are weighted by each owner's sample count.
WEIGHTED_MEANS = {"mean_extracted_bytes_before_filter": "source_size_sample_count",
                  "mean_retained_source_bytes": "source_size_sample_count"}


class OwnerSummarizer(Protocol):
    def summarize(self, run: VerifiedRun, owner: Owner) -> Summary: ...


def combine_summaries(summaries: list[Summary]) -> Summary:
    total = {k: sum(s[k] for s in summaries) for k in COUNTS}
    for name, (numerator, denominator) in RATES.items():
        total[name] = total[numerator] / total[denominator] if total[denominator] else None
    for name, weight in WEIGHTED_MEANS.items():
        known = [s for s in summaries if s[weight]]
        if any(s[name] is None for s in known):
            raise ValueError(f"{name} is missing for an owner with samples")
        total[name] = (sum(s[name] * s[weight] for s in known) / total[weight]) if total[weight] else None
    return {k: total[k] for k in get_type_hints(Summary)}


def merge_scopes(runs: list[VerifiedRun]) -> Scope:
    ranges = [c for r in runs for c in r.scope["scope"]["ranges"]]
    papers = [p for r in runs for p in r.scope["scope"]["paper_tasks"]]
    # Group discovery is complete only when all five assigned ranges are present and done.
    covers_all = sorted(c["owner"] for c in ranges) == sorted(OWNERS)
    return {"ranges": ranges, "paper_tasks": papers,
            "discovery_complete": covers_all and all(r.scope["scope"]["discovery_complete"] for r in runs),
            "versions_complete": bool(runs) and all(r.scope["scope"]["versions_complete"] for r in runs),
            "owner_count": len({p["owner"] for p in papers}), "paper_count": len(papers)}


class CollectionStatisticsBuilder:
    def __init__(self, summarizer: OwnerSummarizer):
        self.summarizer = summarizer

    def build(self, collection: VerifiedCollection) -> CollectionStatistics:
        by_owner: dict[Owner, Summary] = {}
        for run in collection.runs:
            # Like per-run T6 statistics, only owners with selected papers get a summary.
            for owner in sorted({p["owner"] for p in run.scope["scope"]["paper_tasks"]}):
                by_owner[owner] = self.summarizer.summarize(run, owner)
        present = {o for m in collection.manifest["members"] for o in m["owners"]}
        manifest = collection.manifest
        stats: CollectionStatistics = {
            "format_version": 1, "kind": "arxiv-collection-statistics", "schema_version": SCHEMA_VERSION,
            "collection_id": manifest["collection_id"], "is_mock": manifest["is_mock"],
            "sources": manifest["members"], "scope": merge_scopes(collection.runs),
            "missing_owners": [c["owner"] for c in ASSIGNED_RANGES if c["owner"] not in present],
            "summary": combine_summaries(list(by_owner.values())), "by_owner": by_owner}
        problems = validate_pipeline_document(stats, "collection-statistics")
        if problems:
            raise ValueError("Collection statistics are inconsistent: " + problems[0])
        return stats
