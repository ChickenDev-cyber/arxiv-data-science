"""T1: build verified PaperTasks from assigned ID ranges."""

import json
import re
import time
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from typing import Callable, Protocol
from copy import deepcopy

from ..config import OWNERS
from ..contracts import SCHEMA_VERSION, Context, RangeConfig, Result
from ..pipeline_contracts import CheckpointStore
from ..json_utils import canonical
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
    def __init__(self, client: QueryClient, *, batch_size=100, max_candidates=None,
                 checkpoints: CheckpointStore | None = None):
        if type(batch_size) is not int or not 1 <= batch_size <= 100:
            raise ValueError("batch_size must be an integer from 1 to 100")
        if max_candidates is not None and (type(max_candidates) is not int or max_candidates < 1):
            raise ValueError("max_candidates must be a positive integer or None")
        self.client, self.batch_size, self.max_candidates = client, batch_size, max_candidates
        self.checkpoints = checkpoints

    def _inspect(self, ids):
        try:
            return parse_feed(self.client.query(ids), ids)
        except RemoteError as exc:
            return exc

    def _resolve_ids(self, ids, on_progress):
        found, excluded, unresolved = set(), set(), set(ids)
        first_error = None
        response = self._inspect(ids)
        if not isinstance(response, RemoteError):
            found.update(response.found)
            unresolved.difference_update(found)
            missing = set(ids) - found
            if response.complete:
                excluded.update(missing)
                unresolved.clear()
                on_progress(found, excluded, unresolved, None)
                return found, excluded, unresolved, None
        else:
            missing = set(ids)
        if len(ids) > 1 and (not isinstance(response, RemoteError)
                              or response.info["code"] == "invalid_payload"):
            on_progress(found, excluded, unresolved, None)
            for arxiv_id in sorted(missing):
                single = self._inspect([arxiv_id])
                if isinstance(single, RemoteError):
                    unresolved.add(arxiv_id)
                    first_error = first_error or single.info
                elif arxiv_id in single.found:
                    found.add(arxiv_id)
                    unresolved.discard(arxiv_id)
                elif single.complete:
                    excluded.add(arxiv_id)
                    unresolved.discard(arxiv_id)
                else:
                    unresolved.add(arxiv_id)
                    first_error = first_error or RemoteError(
                        "invalid_payload", "Singleton response cannot establish absence").info
                on_progress(found, excluded, unresolved, first_error)
        else:
            unresolved.update(missing)
            if missing:
                first_error = (response.info if isinstance(response, RemoteError) else RemoteError(
                    "invalid_payload", "Response pagination is incomplete or missing").info)
            on_progress(found, excluded, unresolved, first_error)
        return found, excluded, unresolved, first_error

    def _read_batch(self, key, expected):
        record = self.checkpoints.load("discovery_batches", key) if self.checkpoints is not None else None
        if record is None:
            return {**expected, "found_ids": [], "excluded_ids": [],
                    "unresolved_ids": list(expected["candidate_ids"]), "error": None}
        payload = record.get("payload")
        groups = {"found_ids", "excluded_ids", "unresolved_ids"}
        if (record.get("namespace") != "discovery_batches" or record.get("key") != key
                or type(record.get("format_version")) is not int or record["format_version"] != 1
                or not isinstance(payload, dict) or set(payload) != set(expected) | groups | {"error"}
                or canonical({k: payload[k] for k in expected}) != canonical(expected)):
            raise ValueError("Discovery checkpoint belongs to another scope or has invalid fields")
        classified = []
        for field in ("found_ids", "excluded_ids", "unresolved_ids"):
            ids = payload[field]
            if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids):
                raise ValueError("Invalid discovery checkpoint IDs")
            classified.extend(ids)
        if len(classified) != len(set(classified)) or set(classified) != set(expected["candidate_ids"]):
            raise ValueError("Discovery checkpoint does not partition its candidates")
        if ((not payload["unresolved_ids"] and payload["error"] is not None)
                or (payload["unresolved_ids"] and not isinstance(payload["error"], dict))):
            raise ValueError("Discovery checkpoint error disagrees with unresolved IDs")
        return deepcopy(payload)

    def _resolve_batch(self, config, batch, offset):
        key = canonical([config["owner"], config, self.max_candidates, self.batch_size, offset])
        expected = {"owner": config["owner"], "range_config": deepcopy(config),
                    "max_candidates": self.max_candidates, "batch_size": self.batch_size,
                    "offset": offset, "candidate_ids": list(batch)}
        payload = self._read_batch(key, expected)
        if payload["unresolved_ids"]:
            known_found, known_excluded = set(payload["found_ids"]), set(payload["excluded_ids"])

            def save_progress(found, excluded, unresolved, error):
                if unresolved and error is None:
                    error = RemoteError("invalid_payload", "Batch resolution is incomplete; "
                                        "remaining IDs require verification", True).info
                payload.update(found_ids=sorted(known_found | found),
                               excluded_ids=sorted(known_excluded | excluded),
                               unresolved_ids=sorted(unresolved), error=error if unresolved else None)
                if self.checkpoints is not None:
                    self.checkpoints.save({"format_version": 1, "namespace": "discovery_batches", "key": key,
                                           "payload": payload, "updated_at": _utc_now()})

            self._resolve_ids(payload["unresolved_ids"], save_progress)
        return payload

    def discover(self, config: RangeConfig, context: Context, *,
                 progress: Callable[[int, int], None] | None = None) -> Result:
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
        progress_total = min(total_candidates, self.max_candidates) if self.max_candidates is not None else total_candidates
        if progress is not None:
            progress(0, progress_total)

        while batch := list(islice(candidates, self.batch_size)):
            payload = self._resolve_batch(config, batch, processed)
            processed += len(batch)
            found.update(payload["found_ids"])
            excluded.update(payload["excluded_ids"])
            unresolved.update(payload["unresolved_ids"])
            first_error = first_error or payload["error"]
            if progress is not None:
                progress(processed, progress_total)

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
