"""T4: preserve crawled references and enrich the subset with valid arXiv IDs.

Contract: schema 1.2, section 9 of the team spec ("T4 — References của một bài").
The module only returns a Result; the runner owns every official file.
"""

import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Protocol

from ..config import OWNERS
from ..contracts import SCHEMA_VERSION, Context, PaperTask, Result
from ..discovery.api import RemoteError
from ..discovery.service import validate_context
from ..identifiers import is_valid_arxiv_id, normalize_arxiv_id
from .api import ARXIV_API, S2_BASE, S2_FIELDS, valid_iso_date
from .checkpoint import DateCache, PageCheckpoint

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
    if not is_valid_arxiv_id(task.get("arxiv_id")):
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
                 date_batch_size=100, fields=S2_FIELDS, checkpoints=None, store=None):
        if type(page_size) is not int or not 1 <= page_size <= 1000:
            raise ValueError("page_size must be an integer from 1 to 1000")
        if type(max_pages) is not int or max_pages < 1:
            raise ValueError("max_pages must be a positive integer")
        if type(date_batch_size) is not int or not 1 <= date_batch_size <= 100:
            raise ValueError("date_batch_size must be an integer from 1 to 100")
        if not isinstance(fields, str) or not fields.strip():
            raise ValueError("fields must be a nonempty string")
        if checkpoints is not None and store is not None and checkpoints is not store:
            raise ValueError("pass either checkpoints or store, not both")
        self.reader, self.dates = reader, dates
        self.page_size, self.max_pages, self.date_batch_size = page_size, max_pages, date_batch_size
        self.fields = fields
        # `checkpoints` is the shared pipeline port; `store` remains a backwards-compatible alias.
        self.checkpoints = checkpoints if checkpoints is not None else store
        self.store = self.checkpoints
        self.page_checkpoint = PageCheckpoint(self.checkpoints) if self.checkpoints is not None else None
        self.date_cache = DateCache(self.checkpoints) if self.checkpoints is not None else None

    def _attempts(self):
        return getattr(self.reader, "attempts", 0) + getattr(self.dates, "attempts", 0)

    def fetch_references(self, task: PaperTask, context: Context, *, defer_dates=False) -> Result:
        validate_task(task)
        validate_context(context)
        started_at, clock = _utc_now(), time.monotonic()
        initial_attempts = self._attempts()
        arxiv_id = task["arxiv_id"]

        # 1) Page through Semantic Scholar until it reports no further page. With a store, continue
        #    from the last accepted page of an earlier (interrupted or failed) run.
        received, pages, new_pages, offset, page_error, complete = [], 0, 0, 0, None, False
        first_page_failed, resumed_pages, checkpoint_errors = False, 0, []
        page_records = []
        saved = (self.page_checkpoint.load(task["owner"], arxiv_id, self.page_size, self.fields)
                 if self.page_checkpoint else None)
        if saved is not None:
            page_records, received, following, complete, pages = saved
            resumed_pages = pages
            offset = following if following is not None else 0
        while not complete:
            if new_pages >= self.max_pages:
                page_error = RemoteError(
                    "remote_error", f"Page limit ({self.max_pages}) reached before the reference list ended",
                    retryable=True).info
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
            new_pages += 1
            # Preserve the exact accepted page, offset chain and fetch time for safe resume.
            accepted = deepcopy(items)
            received.extend(accepted)
            page_records.append({"offset": offset, "next_offset": following,
                                 "raw_items": accepted, "fetched_at": _utc_now()})
            complete = following is None
            if self.page_checkpoint:
                try:
                    self.page_checkpoint.save(task["owner"], arxiv_id, self.page_size,
                                              self.fields, page_records)
                except OSError as exc:
                    checkpoint_errors.append(f"pages: {type(exc).__name__}")
            if complete:
                break
            offset = following

        if first_page_failed:
            provenance = self._provenance(pages, 0)
            provenance['error_source'] = 'semantic_scholar'
            return self._finish(task, context, started_at, clock, initial_attempts,
                                status="not_found" if page_error["code"] == "not_found" else "failed",
                                data=None, error=page_error, provenance=provenance, metrics={})

        # 2) Keep arXiv references only; merge duplicates field by field.
        merged, notes = self._merge(received)

        # 3) arXiv submission date, only from the arXiv API.
        if defer_dates:
            # The runner buffers this draft in memory; it is never published until
            # batch-wide arXiv dates are resolved. S2 page checkpoints are durable.
            dates, date_error, failed_batches, cached_dates = {}, None, 0, 0
        else:
            dates, date_error, failed_batches, cached_dates = self._submission_dates(list(merged), checkpoint_errors)
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

        provenance = self._provenance(new_pages, failed_batches)
        if error is not None:
            provenance['error_source'] = 'semantic_scholar' if page_error is not None else 'arxiv'
        if self.store is not None:
            provenance.update({"pages_resumed": str(resumed_pages), "dates_from_cache": str(cached_dates)})
            if checkpoint_errors:
                provenance["checkpoint_write_errors"] = "; ".join(checkpoint_errors)
        provenance.update(notes)
        if any(record.get(field) is None for record in merged.values()
               for field in ("title", "authors", "semantic_scholar_id")):
            provenance["metadata_enrichment"] = "source_fields_unavailable"
        return self._finish(task, context, started_at, clock, initial_attempts, status=status,
                            data={"references": references, "raw_references": received, "complete": complete},
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

    def fetch_references_batch(self, tasks, context, *, progress=None):
        """Buffer S2 reference pages per paper, then share arXiv date lookups.

        Only the runner may call this method. S2 page checkpoints still persist
        after each accepted page; the runner publishes finished Results one by
        one using its existing SQLite/filesystem transaction. A 429 prevents
        prefetching more papers until the runner has handled that result.
        """
        results = []
        for task in tasks:
            result = self.fetch_references(task, context, defer_dates=True)
            results.append(result)
            if progress is not None:
                progress(f"References | đã lấy S2 {len(results)}/{len(tasks)} bài: "
                         f"{task['arxiv_id']} ({result['status']})")
            err = result['error']
            if (err is not None and err.get('http_status') == 429
                    and (result['data'] is None or not result['data']['complete'])):
                break

        all_ids = list(dict.fromkeys(
            ref_id for result in results if result['data'] is not None
            for ref_id in result['data']['references']))
        was_cached = self.date_cache.get_many(all_ids) if self.date_cache else {}
        attempt_start, date_start = self._attempts(), time.monotonic()
        failed_id_sets, checkpoint_errors = [], []
        if all_ids:
            dates, date_error, _, _ = self._submission_dates(
                all_ids, checkpoint_errors, failed_id_sets=failed_id_sets)
        else:
            dates, date_error = {}, None
        date_attempts = self._attempts() - attempt_start
        date_share = (time.monotonic() - date_start) / max(1, len(results))

        # Attribute shared HTTP attempts to one Result exactly once so batch
        # totals remain meaningful; record the distribution in provenance.
        recipient = next((r for r in results if r['data'] is not None), None)
        if recipient is not None:
            recipient['attempts'] += date_attempts

        for result in results:
            if result['data'] is None:  # S2 failed on first page: preserve error.
                continue
            refs = result['data']['references']
            for ref_id, ref in refs.items():
                ref['submission_date'] = dates.get(ref_id)
            missing = [_pointer('data', 'references', ref_id, field)
                       for ref_id, ref in refs.items()
                       for field in REQUIRED_FIELDS if ref[field] is None]
            result['missing_fields'] = missing
            result['metrics']['complete_reference_count'] = sum(
                all(ref[field] is not None for field in REQUIRED_FIELDS)
                for ref in refs.values())
            if not result['data']['complete']:
                status = 'partial'
            elif not refs:
                status = 'empty'
            elif missing:
                status = 'partial'
            else:
                status = 'success'
            page_error = result['error']
            failed_here = sum(bool(set(refs).intersection(ids)) for ids in failed_id_sets)
            result['error'] = page_error or (date_error if failed_here else None)
            if status in ('success', 'empty'):
                result['error'] = None
            result['status'] = status
            result['provenance']['date_lookup_batches_failed'] = str(failed_here)
            result['provenance']['cross_paper_date_batch'] = 'true'
            result['provenance']['date_batch_size_papers'] = str(len(results))
            if self.date_cache is not None:
                result['provenance']['dates_from_cache'] = str(
                    sum(ref_id in was_cached for ref_id in refs))
            if checkpoint_errors:
                earlier = result['provenance'].get('checkpoint_write_errors')
                result['provenance']['checkpoint_write_errors'] = '; '.join(
                    ([earlier] if earlier else []) + checkpoint_errors)
            result['duration_seconds'] += date_share
            result['finished_at'] = _utc_now()
        return results

    def _submission_dates(self, ids, checkpoint_errors=None, failed_id_sets=None):
        """Dates from the cache first, then arXiv for the rest; every good batch is cached at once.

        Returns (dates, first_error, failed_batches, cached_count).
        """
        dates, first_error, failed = {}, None, 0
        if self.date_cache:
            dates = self.date_cache.get_many(ids)
        cached = len(dates)
        todo = [i for i in ids if i not in dates]
        for start in range(0, len(todo), self.date_batch_size):
            batch = todo[start:start + self.date_batch_size]
            try:
                answer = self.dates.submission_dates(batch)
                # An adapter may be shared or replaced by the runner, so re-check what it returns:
                # only requested IDs, only valid ISO dates. Anything else counts as a missing date.
                good = {k: v for k, v in answer.items() if k in batch and valid_iso_date(v)}
                dates.update(good)
                if self.date_cache:
                    try:
                        self.date_cache.put_many(good)
                    except OSError as exc:
                        if checkpoint_errors is not None:
                            checkpoint_errors.append(f"dates: {type(exc).__name__}")
            except RemoteError as exc:
                failed += 1
                first_error = first_error or exc.info
                if failed_id_sets is not None:
                    failed_id_sets.append(set(batch))
        return dates, first_error, failed, cached

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
