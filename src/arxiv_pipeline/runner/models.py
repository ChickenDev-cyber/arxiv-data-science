"""Internal runner types, stable task identity and processing configuration."""
from dataclasses import dataclass, asdict
import hashlib
import json
from pathlib import Path
from ..config import ASSIGNED_RANGES, OWNERS
from ..contracts import (Owner, Step, Context, RangeConfig, PaperTask, RunScope, ValidationReport,
                         DiscoveryModule, MetadataModule, SourcesModule, ReferencesModule, SCHEMA_VERSION)
from ..discovery.service import validate_context
from ..identifiers import is_valid_arxiv_id
from ..json_utils import canonical


@dataclass(frozen=True)
class RunOptions:
    run_id: str
    output_root: Path
    owners: tuple[Owner, ...]
    is_mock: bool = False
    max_candidates: int | None = 10
    max_papers: int | None = 5

    def __post_init__(self):
        validate_context(self.context)
        if not self.owners or len(set(self.owners)) != len(self.owners) or not set(self.owners) <= OWNERS:
            raise ValueError("owners must be distinct assigned owners")
        for value in (self.max_candidates, self.max_papers):
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError("limits must be positive integers or None")

    @property
    def context(self) -> Context:
        return {"run_id": self.run_id, "output_root": Path(self.output_root), "is_mock": self.is_mock}

    @property
    def ranges(self):
        return [dict(c) for c in ASSIGNED_RANGES if c["owner"] in self.owners]

    def to_dict(self):
        return {**asdict(self), "output_root": str(Path(self.output_root).resolve()),
                "owners": [c["owner"] for c in self.ranges]}


@dataclass(frozen=True)
class TaskSpec:
    key: str
    owner: Owner
    step: Step
    arxiv_id: str | None
    version: int | None
    range_config: RangeConfig | None = None


@dataclass(frozen=True)
class Artifact:
    path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class ModuleSet:
    discovery: DiscoveryModule
    metadata: MetadataModule
    sources: SourcesModule
    references: ReferencesModule


@dataclass(frozen=True)
class RunOutcome:
    exit_code: int
    scope: RunScope
    executed_tasks: int
    reused_tasks: int
    validation: ValidationReport
    quality: ValidationReport


def discovery_spec(config: RangeConfig) -> TaskSpec:
    key = [config["owner"], "discovery", config["start_month"], config["start_id"],
           config["end_month"], config["end_id"], config["inclusive"]]
    return TaskSpec(canonical(key), config["owner"], "discovery", None, None, dict(config))


def paper_spec(task: PaperTask, step: Step, version: int | None = None) -> TaskSpec:
    if task.get("owner") not in OWNERS or not is_valid_arxiv_id(task.get("arxiv_id")):
        raise ValueError("Invalid paper identity")
    if step not in ("metadata", "sources", "references"):
        raise ValueError("Expected a paper step")
    if (step == "sources" and (type(version) is not int or version < 1)) or (step != "sources" and version is not None):
        raise ValueError("Only sources has a positive integer version")
    return TaskSpec(canonical([task["owner"], step, task["arxiv_id"], version]),
                    task["owner"], step, task["arxiv_id"], version)


def config_fingerprint(options: RunOptions) -> str:
    config = options.to_dict()
    for key in ("run_id", "output_root"):
        config.pop(key)
    config.update(schema_version=SCHEMA_VERSION, policy="t5-baseline-1", ranges=options.ranges,
                  module_parameters={"batch_size": 100, "page_size": 100, "max_pages": 50,
                                     "date_batch_size": 100, "max_attempts": 3})
    return hashlib.sha256(canonical(config).encode()).hexdigest()
