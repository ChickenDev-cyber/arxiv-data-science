"""Shared schema 1.2, defined by the team's milestone-1-schema-and-tasks spec.

TypedDict describes structure for consumers; it does not validate runtime data.
"""

from pathlib import Path
from typing import Literal, Protocol, TypedDict

SCHEMA_VERSION = "1.2"
Owner = Literal["trong", "huynh-nhi", "khang", "man-nhi", "hoang"]
Step = Literal["discovery", "metadata", "sources", "references"]
Status = Literal["success", "partial", "empty", "unsupported", "not_found", "failed"]


class RangeConfig(TypedDict):
    owner: Owner
    start_month: str
    start_id: int
    end_month: str
    end_id: int
    inclusive: bool


class PaperTask(TypedDict):
    owner: Owner
    arxiv_id: str


class VersionInfo(TypedDict):
    version: int
    submitted_at: str | None


class Metadata(TypedDict):
    arxiv_id: str
    title: str | None
    authors: list[str] | None
    submission_date: str | None
    revised_dates: list[str]
    venue: str | None
    versions: list[VersionInfo]
    versions_complete: bool


class Reference(TypedDict):
    arxiv_id: str
    title: str | None
    authors: list[str] | None
    submission_date: str | None
    semantic_scholar_id: str | None


class ExcludedPaper(TypedDict):
    arxiv_id: str
    reason: str


class DiscoveryData(TypedDict):
    papers: list[PaperTask]
    unresolved_ids: list[str]
    excluded_ids: list[ExcludedPaper]
    complete: bool


class SourceFile(TypedDict):
    path: str
    size_bytes: int


class SourcesData(TypedDict):
    source_kind: Literal["tar_gzip", "tar", "gzip_tex", "tex", "pdf_only", "other", "unknown"]
    source_dir: str
    files: list[SourceFile]


class SourceMetrics(TypedDict):
    download_bytes: int | None
    extracted_bytes_before_filter: int | None
    retained_source_bytes: int | None
    removed_figure_bytes: int | None
    removed_other_bytes: int | None


class ReferencesData(TypedDict):
    references: dict[str, Reference]
    raw_references: list[dict[str, object]]
    complete: bool


class ReferenceMetrics(TypedDict):
    s2_reference_count: int
    arxiv_reference_count: int
    complete_reference_count: int


class ErrorInfo(TypedDict):
    code: Literal["network_error", "rate_limited", "remote_error", "not_found",
                  "invalid_payload", "invalid_source", "unsafe_archive", "extract_limit"]
    message: str
    retryable: bool
    http_status: int | None


class Result(TypedDict):
    schema_version: str
    run_id: str
    owner: Owner
    is_mock: bool
    step: Step
    arxiv_id: str | None
    version: int | None
    status: Status
    data: DiscoveryData | Metadata | SourcesData | ReferencesData | None
    missing_fields: list[str]  # JSON Pointers rooted at Result, e.g. /data/title.
    error: ErrorInfo | None
    provenance: dict[str, str]
    metrics: SourceMetrics | ReferenceMetrics | dict[str, int | float | None]
    started_at: str
    finished_at: str
    duration_seconds: float
    attempts: int


class Context(TypedDict):
    run_id: str
    is_mock: bool
    output_root: Path


class Issue(TypedDict):
    owner: Owner
    arxiv_id: str | None
    version: int | None
    step: Step
    field: str | None
    message: str


class ValidationReport(TypedDict):
    valid: bool
    issues: list[Issue]


class Scope(TypedDict):
    ranges: list[RangeConfig]
    paper_tasks: list[PaperTask]
    discovery_complete: bool
    versions_complete: bool
    owner_count: int
    paper_count: int


class RunScope(TypedDict):
    schema_version: str
    run_id: str
    is_mock: bool
    scope: Scope


class Summary(TypedDict):
    papers_discovered: int
    metadata_complete_papers: int
    papers_complete: int
    paper_success_rate: float | None
    source_versions_expected: int
    source_versions_success: int
    source_versions_unsupported: int
    source_versions_unresolved: int
    references_fully_fetched_papers: int
    references_query_success_rate: float | None
    references_metadata_complete_papers: int
    metadata_success_rate: float | None
    source_success_rate: float | None
    references_arxiv_total: int
    references_complete_total: int
    reference_metadata_success_rate: float | None
    mean_arxiv_references_per_paper: float | None
    source_size_sample_count: int
    mean_extracted_bytes_before_filter: float | None
    mean_retained_source_bytes: float | None


class Statistics(TypedDict):
    schema_version: str
    run_id: str
    is_mock: bool
    scope: Scope
    summary: Summary
    by_owner: dict[Owner, Summary]


class Benchmark(TypedDict):
    schema_version: str
    is_mock: bool
    run_id: str
    scope: Scope
    environment: dict[str, str]
    cache_mode: Literal["cold", "warm", "mixed"]
    wall_seconds: float
    peak_ram_bytes: int | None
    peak_disk_bytes: int | None
    final_output_bytes: int
    method: str
    mean_ram_bytes: int | None
    sampling_interval_seconds: float
    exit_code: int


class DiscoveryModule(Protocol):
    def discover(self, config: RangeConfig, context: Context) -> Result: ...


class MetadataModule(Protocol):
    def fetch_metadata(self, task: PaperTask, context: Context) -> Result: ...


class SourcesModule(Protocol):
    def fetch_sources(self, task: PaperTask, version: int, context: Context) -> Result: ...


class ReferencesModule(Protocol):
    def fetch_references(self, task: PaperTask, context: Context) -> Result: ...


class StatisticsModule(Protocol):
    def build_statistics(self, output_root: Path, run_id: str) -> Statistics: ...


def paper_folder(arxiv_id: str) -> str:
    """Canonical ID to folder; input must already be normalized and validated."""
    return arxiv_id.replace("/", "__").replace(".", "-")
