"""T2: metadata and verified version history for one PaperTask."""

import re
import time
from datetime import datetime, timezone
from typing import Protocol

from ..config import OWNERS
from ..contracts import SCHEMA_VERSION, Context, PaperTask, Result
from ..discovery.api import RemoteError
from ..discovery.service import validate_context
from .api import parse_record, version_date_order_issues

# Modern IDs: YYMM.NNNN from 0704 to 1412, YYMM.NNNNN from 1501 (2503.06914).
# Old IDs keep "/": archive/YYMMNNN from 9108 to 0703 (hep-th/9901001, math/0309136).
# A subject class such as math.GT/0309136 is a display form, not the canonical ID OAI knows.
MODERN_ID = re.compile(r"(\d{2})(\d{2})\.(\d{4,5})")
OLD_ID = re.compile(r"[a-z]+(?:-[a-z]+)*/(\d{2})(\d{2})(\d{3})")


def is_valid_arxiv_id(value):
    if not isinstance(value, str):
        return False
    if match := MODERN_ID.fullmatch(value):
        year, month, number = int(match[1]), int(match[2]), match[3]
        digits = 5 if year >= 15 else 4
        return 1 <= month <= 12 and (year, month) >= (7, 4) and len(number) == digits and int(number) >= 1
    if match := OLD_ID.fullmatch(value):
        year, month, number = int(match[1]), int(match[2]), int(match[3])
        yymm = (year + (1900 if year >= 91 else 2000), month)
        return 1 <= month <= 12 and (1991, 8) <= yymm <= (2007, 3) and number >= 1
    return False


class RecordClient(Protocol):
    attempts: int

    def record(self, arxiv_id: str) -> bytes: ...

    def provenance(self, arxiv_id: str) -> dict[str, str]: ...


def validate_task(task):
    if not isinstance(task, dict) or set(task) != {"owner", "arxiv_id"}:
        raise ValueError("PaperTask must have exactly owner and arxiv_id")
    if task["owner"] not in OWNERS:
        raise ValueError("owner must match an assigned owner")
    if not is_valid_arxiv_id(task["arxiv_id"]):
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
        provenance = dict(self.client.provenance(task["arxiv_id"]))
        if data is not None:
            date_issues = version_date_order_issues(data["versions"])
            if date_issues:
                provenance["version_date_order"] = "; ".join(date_issues)
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": context["run_id"], "owner": task["owner"], "is_mock": context["is_mock"],
            "step": "metadata", "arxiv_id": task["arxiv_id"], "version": None,
            "status": status, "data": data, "missing_fields": missing,
            "error": error,
            "provenance": provenance,
            "metrics": {}, "started_at": started_at, "finished_at": _utc_now(),
            "duration_seconds": max(0.0, time.monotonic() - start),
            "attempts": self.client.attempts - initial_attempts,
        }
