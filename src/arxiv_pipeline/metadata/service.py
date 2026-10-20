"""T2: metadata and verified version history for one PaperTask."""

import re
import time
from datetime import datetime, timezone
from typing import Protocol

from ..config import OWNERS
from ..contracts import SCHEMA_VERSION, Context, PaperTask, Result
from ..discovery.api import RemoteError
from ..discovery.service import validate_context
from .api import OAI_URL, parse_record, record_url

# Modern IDs (2503.06914) and old IDs that keep "/" (hep-th/9901001, math.GT/0309136).
ARXIV_ID = re.compile(r"\d{4}\.\d{4,5}|[a-z]+(-[a-z]+)*(\.[A-Z]{2})?/\d{7}")


class RecordClient(Protocol):
    attempts: int

    def record(self, arxiv_id: str) -> bytes: ...


def validate_task(task):
    if not isinstance(task, dict) or set(task) != {"owner", "arxiv_id"}:
        raise ValueError("PaperTask must have exactly owner and arxiv_id")
    if task["owner"] not in OWNERS:
        raise ValueError("owner must match an assigned owner")
    if not isinstance(task["arxiv_id"], str) or not ARXIV_ID.fullmatch(task["arxiv_id"]):
        raise ValueError("arxiv_id must be a canonical ID without URL or version suffix")


def missing_fields(data):
    """JSON Pointers (from the Result root) of required metadata that is still unknown."""
    missing = []
    if not data["title"]:
        missing.append("/data/title")
    if not data["authors"]:
        missing.append("/data/authors")
    versions = data["versions"]
    if not versions:
        return missing + ["/data/versions"]
    if data["submission_date"] is None:
        missing.append("/data/submission_date")
    missing += [f"/data/versions/{i}/submitted_at" for i, v in enumerate(versions) if v["submitted_at"] is None]
    if [v["version"] for v in versions] != list(range(1, len(versions) + 1)):
        missing.append("/data/versions")  # v1 not found or a gap; never insert unverified versions
    return missing


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class MetadataModule:
    def __init__(self, client: RecordClient):
        self.client = client

    def fetch_metadata(self, task: PaperTask, context: Context) -> Result:
        validate_task(task)
        validate_context(context)
        started_at, start = _utc_now(), time.monotonic()
        initial_attempts = self.client.attempts
        data, error = None, None
        try:
            data = parse_record(self.client.record(task["arxiv_id"]), task["arxiv_id"])
        except RemoteError as exc:
            # Expected service/payload failures only; a network error is never not_found.
            error = exc.info

        if data is None:
            status, missing = ("not_found" if error["code"] == "not_found" else "failed"), []
        else:
            missing = missing_fields(data)
            status = "success" if not missing and data["versions_complete"] else "partial"
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": context["run_id"], "owner": task["owner"], "is_mock": context["is_mock"],
            "step": "metadata", "arxiv_id": task["arxiv_id"], "version": None,
            "status": status, "data": data, "missing_fields": missing,
            "error": error,
            "provenance": {"source": OAI_URL, "url": record_url(task["arxiv_id"]),
                           "metadata_prefix": "arXivRaw",
                           "method": "one OAI GetRecord per paper; versions and dates as listed by arXivRaw"},
            "metrics": {}, "started_at": started_at, "finished_at": _utc_now(),
            "duration_seconds": max(0.0, time.monotonic() - start),
            "attempts": self.client.attempts - initial_attempts,
        }

