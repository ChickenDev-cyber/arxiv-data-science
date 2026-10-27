"""Shared pipeline ports and data shapes; no storage or network operations.

Result data stays on schema 1.2. ``format_version`` versions operational
documents independently of that schema and SQLite's ``user_version``.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import ContextManager, Literal, Protocol, TypedDict

from .contracts import ErrorInfo, Owner, RangeConfig, Result, RunScope, Scope, Summary, ValidationReport


Stage = Literal["discovery", "sources", "references", "all"]
RunState = Literal["created", "running", "paused", "finished", "error"]
CheckpointNamespace = Literal["discovery_batches", "references", "arxiv_dates", "rate_limits"]


class DiscoveryBatchCheckpoint(TypedDict):
    owner: Owner
    range_config: RangeConfig
    max_candidates: int | None
    batch_size: int
    offset: int
    candidate_ids: list[str]
    found_ids: list[str]
    excluded_ids: list[str]
    unresolved_ids: list[str]
    error: ErrorInfo | None


class ReferencePage(TypedDict):
    offset: int
    next_offset: int | None
    raw_items: list[dict[str, object]]
    fetched_at: str


class ReferencesCheckpoint(TypedDict):
    owner: Owner
    arxiv_id: str
    page_size: int
    fields: str
    pages: list[ReferencePage]


class ArxivDateCheckpoint(TypedDict):
    arxiv_id: str
    submission_date: str
    source: str
    fetched_at: str


class RateLimitCheckpoint(TypedDict):
    group: Literal["arxiv", "semantic_scholar"]
    next_allowed_at: str
    recent_requests_at: list[str]
    consecutive_429: int


CheckpointPayload = DiscoveryBatchCheckpoint | ReferencesCheckpoint | ArxivDateCheckpoint | RateLimitCheckpoint


class CheckpointRecord(TypedDict):
    format_version: Literal[1]
    namespace: CheckpointNamespace
    key: str
    payload: CheckpointPayload
    updated_at: str


class FileDigest(TypedDict):
    path: str
    size_bytes: int
    sha256: str


class SavedRunConfig(TypedDict):
    run_id: str
    output_root: str
    owners: list[Owner]
    is_mock: bool
    max_candidates: int | None
    max_papers: int | None


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    schema_version: str
    fingerprint: str
    config: SavedRunConfig
    status: RunState
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class VerifiedRun:
    output_root: Path
    scope: RunScope
    accepted_results: list[Result]
    validation: ValidationReport
    quality: ValidationReport
    files: list[FileDigest]
    run_state: Literal["paused", "finished"]
    config_fingerprint: str
    recorded_output_root: str


class SnapshotManifest(TypedDict):
    format_version: Literal[1]
    kind: Literal["arxiv-snapshot"]
    snapshot_id: str
    schema_version: Literal["1.2"]
    run_id: str
    is_mock: bool
    owners: list[Owner]
    created_at: str
    exporter_commit: str
    producer_commits: list[str]
    unknown_provenance_tasks: int
    config_fingerprint: str
    origin_output_root: str
    files: list[FileDigest]


class CollectionMember(TypedDict):
    snapshot_id: str
    run_id: str
    owners: list[Owner]
    exporter_commit: str
    root: str
    manifest_path: str


class CollectionManifest(TypedDict):
    format_version: Literal[1]
    kind: Literal["arxiv-collection"]
    collection_id: str
    schema_version: Literal["1.2"]
    is_mock: bool
    created_at: str
    members: list[CollectionMember]


@dataclass(frozen=True)
class VerifiedCollection:
    manifest: CollectionManifest
    runs: list[VerifiedRun]


class CollectionStatistics(TypedDict):
    format_version: Literal[1]
    kind: Literal["arxiv-collection-statistics"]
    schema_version: Literal["1.2"]
    collection_id: str
    is_mock: bool
    sources: list[CollectionMember]
    scope: Scope
    missing_owners: list[Owner]
    summary: Summary
    by_owner: dict[Owner, Summary]


class CheckpointStore(Protocol):
    def load(self, namespace: str, key: str) -> CheckpointRecord | None: ...

    def save(self, record: CheckpointRecord) -> None: ...

    def delete(self, namespace: str, key: str) -> None: ...


class RunReader(Protocol):
    def open(self, output_root: Path, run_id: str) -> ContextManager[VerifiedRun]: ...


class StateRelocator(Protocol):
    def relocate(self, output_root: Path, run_id: str, expected_old_root: str,
                 *, snapshot_id: str) -> None: ...


class SnapshotReader(Protocol):
    def unpack(self, archive: Path, destination: Path) -> SnapshotManifest: ...


class CollectionReader(Protocol):
    def open(self, collection_root: Path) -> ContextManager[VerifiedCollection]: ...
