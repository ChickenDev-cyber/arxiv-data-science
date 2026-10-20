"""T1: build verified PaperTasks from assigned ID ranges."""

import json
import re
import time
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from typing import Protocol

from ..config import OWNERS
from ..contracts import SCHEMA_VERSION, Context, RangeConfig, Result
from .api import API_URL, RemoteError, parse_feed


class QueryClient(Protocol):
    attempts: int

    def query(self, ids: list[str]) -> bytes: ...


def _month_number(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}", value):
        raise ValueError("month must use YYYY-MM")
    year, month = map(int, value.split("-"))
    if not 2015 <= year <= 2099 or not 1 <= month <= 12:
        raise ValueError("T1 supports months from 2015-01 to 2099-12")
    return year * 12 + month - 1


def _bounds(config):
    if config.get("owner") not in OWNERS:
        raise ValueError("owner must match an assigned owner")
    first = _month_number(config.get("start_month"))
    last = _month_number(config.get("end_month"))
    for key in ("start_id", "end_id"):
        if type(config.get(key)) is not int or not 1 <= config[key] <= 99999:
            raise ValueError(f"{key} must be an integer from 1 to 99999")
    if type(config.get("inclusive")) is not bool:
        raise ValueError("inclusive must be a boolean")
    if (first, config["start_id"]) > (last, config["end_id"]):
        raise ValueError("range start must not be after range end")
    return first, last


def _segments(config):
    first, last = _bounds(config)
    for month in range(first, last + 1):
        low = config["start_id"] if month == first else 1
        high = config["end_id"] if month == last else 99999
        if not config["inclusive"]:
            low += month == first
            high -= month == last
        yield month, low, high


def candidate_count(config: RangeConfig) -> int:
    return sum(max(0, high - low + 1) for _, low, high in _segments(config))


def candidate_ids(config: RangeConfig):
    for month, low, high in _segments(config):
        year, month_zero = divmod(month, 12)
        prefix = f"{year % 100:02d}{month_zero + 1:02d}"
        for number in range(low, high + 1):
            yield f"{prefix}.{number:05d}"


def validate_context(context):
    run_id = context.get("run_id")
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
        raise ValueError("run_id may contain only letters, digits, _ and -")
    if type(context.get("is_mock")) is not bool:
        raise ValueError("is_mock must be a boolean")
    root = context.get("output_root")
    if not isinstance(root, (str, Path)) or not str(root).strip():
        raise ValueError("output_root must be a nonempty path")


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class DiscoveryModule:
    def __init__(self, client: QueryClient, *, batch_size=100, max_candidates=None):
        if type(batch_size) is not int or not 1 <= batch_size <= 100:
            raise ValueError("batch_size must be an integer from 1 to 100")
        if max_candidates is not None and (type(max_candidates) is not int or max_candidates < 1):
            raise ValueError("max_candidates must be a positive integer or None")
        self.client, self.batch_size, self.max_candidates = client, batch_size, max_candidates

    def discover(self, config: RangeConfig, context: Context) -> Result:
        total_candidates = candidate_count(config)
        validate_context(context)
        started_at, start = _utc_now(), time.monotonic()
        initial_attempts = self.client.attempts
        limited = self.max_candidates is not None and self.max_candidates < total_candidates
        candidates = candidate_ids(config)
        if self.max_candidates is not None:
            candidates = islice(candidates, self.max_candidates)
        found, excluded, unresolved = set(), set(), set()
        first_error = None
        processed = 0

        def inspect(ids):
            try:
                return parse_feed(self.client.query(ids), ids)
            except RemoteError as exc:
                # Expected service/payload failures only, not arbitrary code errors.
                return exc

        while batch := list(islice(candidates, self.batch_size)):
            processed += len(batch)
            response = inspect(batch)
            if not isinstance(response, RemoteError):
                found.update(response.found)
                missing = set(batch) - response.found
                if response.complete:
                    excluded.update(missing)
                    continue
            else:
                missing = set(batch)

            # Ambiguous payload/pagination can be disambiguated once per ID.
            # Network/HTTP failures already exhausted the client's retry budget.
            if len(batch) > 1 and (not isinstance(response, RemoteError)
                                  or response.info["code"] == "invalid_payload"):
                for arxiv_id in sorted(missing):
                    single = inspect([arxiv_id])
                    if isinstance(single, RemoteError):
                        unresolved.add(arxiv_id)
                        first_error = first_error or single.info
                    elif arxiv_id in single.found:
                        found.add(arxiv_id)
                    elif single.complete:
                        excluded.add(arxiv_id)
                    else:
                        unresolved.add(arxiv_id)
                        first_error = first_error or RemoteError(
                            "invalid_payload", "Singleton response cannot establish absence").info
            else:
                unresolved.update(missing)
                if missing:
                    first_error = first_error or (response.info if isinstance(response, RemoteError) else
                        RemoteError("invalid_payload", "Response pagination is incomplete or missing").info)

        complete = not limited and not unresolved
        useful = bool(found or excluded) or processed == 0
        status = "success" if complete else "partial" if useful else "failed"
        data = None if status == "failed" else {
            "papers": [{"owner": config["owner"], "arxiv_id": i} for i in sorted(found)],
            "unresolved_ids": sorted(unresolved),
            "excluded_ids": [{"arxiv_id": i, "reason": "not_returned_by_complete_api_feed"}
                             for i in sorted(excluded)],
            "complete": complete,
        }
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": context["run_id"], "owner": config["owner"], "is_mock": context["is_mock"],
            "step": "discovery", "arxiv_id": None, "version": None,
            "status": status, "data": data, "missing_fields": [],
            "error": first_error,
            "provenance": {"source": API_URL, "scope_limited": str(limited).lower(),
                           "candidate_count": str(total_candidates), "candidates_checked": str(processed),
                           "range_config": json.dumps(config, sort_keys=True),
                           "method": "id_list batches; absence only from complete validated feeds"},
            "metrics": {}, "started_at": started_at, "finished_at": _utc_now(),
            "duration_seconds": max(0.0, time.monotonic() - start),
            "attempts": self.client.attempts - initial_attempts,
        }
