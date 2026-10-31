"""Shared configuration and injected validation for portable snapshots."""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from importlib import import_module
from typing import Callable, cast

from ..pipeline_contracts import SnapshotManifest

ManifestValidator = Callable[[object], list[str]]


@dataclass(frozen=True)
class SnapshotLimits:
    max_files: int = 100_000
    max_file_bytes: int = 2 * 1024**3
    max_total_bytes: int = 20 * 1024**3

    def __post_init__(self):
        for value in (self.max_files, self.max_file_bytes, self.max_total_bytes):
            if type(value) is not int or value <= 0:
                raise ValueError("Snapshot limits must be positive integers")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_manifest_validator() -> ManifestValidator:
    """Resolve the member-owned validator without copying its schema rules."""
    name = "arxiv_pipeline.validation.pipeline_contracts"
    try:
        module = import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:
            raise
        raise ValueError(
            "Snapshot operations require the shared pipeline document validator; "
            "integrate the validation module or inject a ManifestValidator."
        ) from exc
    return partial(module.validate_pipeline_document, kind="snapshot")


def check_manifest(value: object, validator: ManifestValidator) -> SnapshotManifest:
    problems = validator(deepcopy(value))
    if not isinstance(problems, list) or any(not isinstance(p, str) for p in problems):
        raise TypeError("ManifestValidator must return list[str]")
    if problems:
        raise ValueError("Invalid snapshot manifest: " + "; ".join(problems))
    return cast(SnapshotManifest, deepcopy(value))
