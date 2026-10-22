"""T4: references of one paper that carry an arXiv ID, with the metadata the team needs.

Contract: schema 1.1, section 9 of the team spec ("T4 — References của một bài").
The module only returns a Result; the runner owns every official file.
"""

import time
from datetime import datetime, timezone
from typing import Protocol

from ..config import OWNERS
from ..contracts import SCHEMA_VERSION, Context, PaperTask, Result
from ..discovery.api import RemoteError
from ..discovery.service import validate_context
from .api import ARXIV_API, S2_BASE, is_canonical_id, normalize_arxiv_id, valid_iso_date

REQUIRED_FIELDS = ("title", "authors", "submission_date", "semantic_scholar_id")


class ReferencesReader(Protocol):
    attempts: int

    def references_page(self, arxiv_id: str, offset: int, limit: int) -> dict: ...


class DateReader(Protocol):
    attempts: int

    def submission_dates(self, ids: list[str]) -> dict[str, str]: ...


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _pointer(*tokens):
    return "/" + "/".join(str(t).replace("~", "~0").replace("/", "~1") for t in tokens)


def validate_task(task):
    if not isinstance(task, dict) or task.get("owner") not in OWNERS:
        raise ValueError("task.owner must match an assigned owner")
    if not is_canonical_id(task.get("arxiv_id")):
        raise ValueError("task.arxiv_id must be a canonical arXiv ID (no URL, no version suffix)")


def _clean_title(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def _clean_authors(value):
    if not isinstance(value, list):
        return None
    names = [a.get("name").strip() for a in value
             if isinstance(a, dict) and isinstance(a.get("name"), str) and a["name"].strip()]
    return names or None  # no usable author name = unknown, not "confirmed none"


def _clean_s2_id(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def _read_page(payload, offset):
    """Validate one Semantic Scholar page; return (items, next_offset or None)."""
    items = payload["data"]
    if any(not isinstance(item, dict) for item in items):
        raise RemoteError("invalid_payload", "Reference page contains a non-object item")
    following = payload.get("next")
    if following is None:
        return items, None
    if type(following) is not int or following <= offset:
        raise RemoteError("invalid_payload", "Reference page has an invalid 'next' offset")
    return items, following


class ReferencesModule:
    def __init__(self, reader: ReferencesReader, dates: DateReader, *, page_size=100, max_pages=50,
                 date_batch_size=100):
        if type(page_size) is not int or not 1 <= page_size <= 1000:
            raise ValueError("page_size must be an integer from 1 to 1000")
        if type(max_pages) is not int or max_pages < 1:
            raise ValueError("max_pages must be a positive integer")
        if type(date_batch_size) is not int or not 1 <= date_batch_size <= 100:
            raise ValueError("date_batch_size must be an integer from 1 to 100")
        self.reader, self.dates = reader, dates
        self.page_size, self.max_pages, self.date_batch_size = page_size, max_pages, date_batch_size

    def _attempts(self):
        return getattr(self.reader, "attempts", 0) + getattr(self.dates, "attempts", 0)

    def fetch_references(self, task: PaperTask, context: Context) -> Result:
        validate_task(task)
        validate_context(context)
        started_at, clock = _utc_now(), time.monotonic()
        initial_attempts = self._attempts()
        arxiv_id = task["arxiv_id"]

        # 1) Page through Semantic Scholar until it reports no further page.
        received, pages, offset, page_error, complete = [], 0, 0, None, False
        first_page_failed = False
        while True:
            if pages >= self.max_pages:
                page_error = RemoteError(
                    "remote_error", f"Page limit ({self.max_pages}) reached before the reference list ended").info
                break
            try:
                payload = self.reader.references_page(arxiv_id, offset, self.page_size)
                if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                    raise RemoteError("invalid_payload", "Reference page has no 'data' list")
                items, following = _read_page(payload, offset)
            except RemoteError as exc:
                page_error, first_page_failed = exc.info, pages == 0
                break
            pages += 1
            received.extend(items)  # a whole page is accepted or rejected, never half of it
            if following is None:
                complete = True
                break
            offset = following

        if first_page_failed:
            return self._finish(task, context, started_at, clock, initial_attempts,
                                status="not_found" if page_error["code"] == "not_found" else "failed",
                                data=None, error=page_error, provenance=self._provenance(pages, 0), metrics={})

        # 2) Keep arXiv references only; merge duplicates field by field.
        merged, notes = self._merge(received)

        # 3) arXiv submission date, only from the arXiv API.
        dates, date_error, failed_batches = self._submission_dates(list(merged))
        for key, record in merged.items():
            record["submission_date"] = dates.get(key)

        references = {k: {"arxiv_id": k, **{f: r[f] for f in REQUIRED_FIELDS}} for k, r in merged.items()}
        missing = [_pointer("data", "references", k, f) for k, r in references.items()
                   for f in REQUIRED_FIELDS if r[f] is None]
        complete_count = sum(all(r[f] is not None for f in REQUIRED_FIELDS) for r in references.values())
        metrics = {"s2_reference_count": len(received), "arxiv_reference_count": len(references),
                   "complete_reference_count": complete_count}

        if not complete:
            status = "partial"
        elif not references:
            status = "empty"
        elif missing:
            status = "partial"
        else:
            status = "success"
        error = page_error or date_error
        if status in ("success", "empty"):
            error = None

        provenance = self._provenance(pages, failed_batches)
        provenance.update(notes)
        return self._finish(task, context, started_at, clock, initial_attempts, status=status,
                            data={"references": references, "complete": complete},
                            error=error, provenance=provenance, metrics=metrics, missing=missing)

    @staticmethod
    def _merge(received):
        """First non-missing value per field wins; whole author lists are never mixed."""
        merged, seen, conflicts, notes = {}, {}, {}, {}
        for index, item in enumerate(received, start=1):
            cited = item.get("citedPaper")
            if not isinstance(cited, dict):
                continue
            external = cited.get("externalIds")
            key = normalize_arxiv_id(external.get("ArXiv")) if isinstance(external, dict) else None
            if key is None:
                continue
            values = {"title": _clean_title(cited.get("title")), "authors": _clean_authors(cited.get("authors")),
                      "semantic_scholar_id": _clean_s2_id(cited.get("paperId"))}
            if key not in merged:
                merged[key] = {f: None for f in ("title", "authors", "semantic_scholar_id")}
                seen[key] = {f: None for f in merged[key]}
                repeated = False
            else:
                repeated = True
            for field, value in values.items():
                if value is None:
                    continue
                if merged[key][field] is None:
                    merged[key][field], seen[key][field] = value, index
                elif repeated and merged[key][field] != value:
                    conflicts.setdefault((key, field), []).append(index)
            if repeated:
                for field in values:
                    notes.setdefault((key, field), True)
        provenance = {}
        for (key, field) in notes:
            chosen = seen[key][field]
            text = f"duplicate arXiv ID; selected s2_item={chosen}" if chosen else "duplicate arXiv ID; no value in any item"
            if (key, field) in conflicts:
                text += "; conflicting values in s2_item=" + ",".join(map(str, conflicts[(key, field)]))
            provenance[_pointer("data", "references", key, field)] = text
        return merged, provenance

    def _submission_dates(self, ids):
        dates, first_error, failed = {}, None, 0
        for start in range(0, len(ids), self.date_batch_size):
            batch = ids[start:start + self.date_batch_size]
            try:
                answer = self.dates.submission_dates(batch)
                # An adapter may be shared or replaced by the runner, so re-check what it returns:
                # only requested IDs, only valid ISO dates. Anything else counts as a missing date.
                dates.update({k: v for k, v in answer.items() if k in batch and valid_iso_date(v)})
            except RemoteError as exc:
                failed += 1
                first_error = first_error or exc.info
        return dates, first_error, failed

    @staticmethod
    def _provenance(pages, failed_batches):
        return {"source": S2_BASE, "reference_source": "semantic-scholar-graph-v1",
                "submission_date_source": ARXIV_API,
                "date_policy": "arXiv API 'published' (v1); Semantic Scholar publicationDate is not used",
                "pages_fetched": str(pages), "date_lookup_batches_failed": str(failed_batches)}

    def _finish(self, task, context, started_at, clock, initial_attempts, *, status, data, error,
                provenance, metrics, missing=()):
        return {
            "schema_version": SCHEMA_VERSION, "run_id": context["run_id"], "owner": task["owner"],
            "is_mock": context["is_mock"], "step": "references", "arxiv_id": task["arxiv_id"],
            "version": None, "status": status, "data": data, "missing_fields": list(missing),
            "error": error, "provenance": provenance, "metrics": metrics,
            "started_at": started_at, "finished_at": _utc_now(),
            "duration_seconds": max(0.0, time.monotonic() - clock),
            "attempts": self._attempts() - initial_attempts,
        }
