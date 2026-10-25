"""T3: arXiv source download and extraction service."""

from datetime import datetime, timezone
from contextlib import nullcontext
from pathlib import Path
import re
import shutil
import tempfile
import time
from typing import cast

from ..config import OWNERS
from ..contracts import (
    SCHEMA_VERSION,
    Context,
    PaperTask,
    Result,
    SourceMetrics,
    SourcesData,
    paper_folder,
)
from ..discovery.api import RemoteError
from ..discovery.service import validate_context
from ..identifiers import is_valid_arxiv_id
from .api import SourceClient
from .extractor import ExtractionLimits, SourceExtractor, SourceProcessingError


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_task(task: object) -> None:
    if not isinstance(task, dict) or set(task) != {"owner", "arxiv_id"}:
        raise ValueError("PaperTask must have exactly owner and arxiv_id")
    if task["owner"] not in OWNERS:
        raise ValueError(f"owner '{task.get('owner')}' must match an assigned owner")
    if not is_valid_arxiv_id(task["arxiv_id"]):
        raise ValueError("arxiv_id must be a canonical ID without URL or version suffix")


def validate_version(version: object) -> None:
    if type(version) is not int or version < 1:
        raise ValueError(f"version must be a positive integer >= 1, got {version!r}")


class SourcesModule:
    """Orchestrates source downloading, format extraction, staging handoff, and Result production."""

    def __init__(
        self,
        client: SourceClient,
        extractor: SourceExtractor,
        *,
        limits: ExtractionLimits | None = None,
    ) -> None:
        self.client = client
        self.extractor = extractor
        self.limits = limits or ExtractionLimits()

    def fetch_sources(self, task: PaperTask, version: int, context: Context) -> Result:
        validate_task(task)
        validate_version(version)
        validate_context(context)

        owner = task["owner"]
        arxiv_id = task["arxiv_id"]
        output_root = Path(context["output_root"])
        folder_name = paper_folder(arxiv_id)

        # Check if staging destination is already occupied
        staging_path = output_root / "staging" / context["run_id"] / owner / folder_name / f"v{version}"
        if staging_path.exists():
            raise FileExistsError(f"Staging destination already exists: {staging_path}")

        source_dir = f"raw/{owner}/{folder_name}/v{version}"

        started_at = _utc_now()
        start_time = time.monotonic()
        initial_attempts = self.client.attempts

        data: SourcesData | None = None
        error = None
        download_bytes: int | None = None
        metrics: SourceMetrics = {
            "download_bytes": None,
            "extracted_bytes_before_filter": None,
            "retained_source_bytes": None,
            "removed_figure_bytes": None,
            "removed_other_bytes": None,
        }

        # Isolated scratch area for download and extraction
        scratch_base = output_root / "scratch" / context["run_id"] / "sources"
        scratch_base.mkdir(parents=True, exist_ok=True)

        budget_context = getattr(self.client, "fetch_budget", nullcontext)
        with tempfile.TemporaryDirectory(dir=scratch_base) as tmp_scratch, budget_context() as budget:
            scratch_file = Path(tmp_scratch) / f"{arxiv_id}v{version}.download"
            scratch_extract = Path(tmp_scratch) / "extracted"

            for processing_attempt in range(3):
                error = None
                download_bytes = None
                metrics["download_bytes"] = None
                try:
                    download_bytes = self.client.download(arxiv_id, version, scratch_file)
                    extraction = self.extractor.extract(
                        scratch_file, scratch_extract, limits=self.limits
                    )
                    metrics = dict(extraction.metrics)
                    metrics["download_bytes"] = download_bytes

                    if extraction.status == "unsupported":
                        status = "unsupported"
                        data = {
                            "source_kind": extraction.source_kind,
                            "source_dir": source_dir,
                            "files": [],
                        }
                    else:
                        staging_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(scratch_extract), str(staging_path))
                        status = "success"
                        data = {
                            "source_kind": extraction.source_kind,
                            "source_dir": source_dir,
                            "files": extraction.files,
                        }
                    break
                except RemoteError as exc:
                    error = exc.info
                    status = "not_found" if error["code"] == "not_found" else "failed"
                    break
                except SourceProcessingError as exc:
                    error = exc.info
                    status = "failed"
                    metrics["download_bytes"] = download_bytes
                    if not error["retryable"] or processing_attempt == 2 or (
                        budget is not None and budget.remaining <= 0
                    ):
                        break
                    scratch_file.unlink(missing_ok=True)
                    if scratch_extract.exists():
                        shutil.rmtree(scratch_extract)

        provenance = dict(self.client.provenance(arxiv_id, version))

        return cast(
            Result,
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": context["run_id"],
                "owner": owner,
                "is_mock": context["is_mock"],
                "step": "sources",
                "arxiv_id": arxiv_id,
                "version": version,
                "status": status,
                "data": data,
                "missing_fields": [],
                "error": error,
                "provenance": provenance,
                "metrics": metrics,
                "started_at": started_at,
                "finished_at": _utc_now(),
                "duration_seconds": max(0.0, time.monotonic() - start_time),
                "attempts": self.client.attempts - initial_attempts,
            },
        )
