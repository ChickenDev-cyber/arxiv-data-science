"""Shared request pacing with durable checkpoints using the pipeline contract."""
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import threading
import time
from typing import Protocol

from .pipeline_contracts import CheckpointRecord

SCHEDULER_NAMESPACE = "rate_limits"
SCHEDULER_FORMAT_VERSION = 1
S2_WINDOW_SECONDS = 300
S2_WINDOW_REQUESTS = 100


def rate_limit_delay(streak, retry_after):
    """Only fallback is capped; a server cooldown can be longer."""
    return max(30. * (2 ** min(max(streak - 1, 0), 2)), retry_after)


def utc_iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc_iso(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.timestamp()
    except (ValueError, OverflowError):
        return None


def canonical_checkpoint_key(*parts):
    return json.dumps(list(parts), ensure_ascii=False, separators=(",", ":"))


def checkpoint_record(namespace, key, payload):
    return {"format_version": 1, "namespace": namespace, "key": key,
            "payload": payload, "updated_at": utc_iso(time.time())}


class CheckpointStore(Protocol):
    """Compatibility protocol for legacy stores; prefer pipeline_contracts.CheckpointStore."""
    def get(self, namespace: str, key: str) -> dict | None: ...
    def put(self, namespace: str, key: str, payload: dict) -> None: ...
    def delete(self, namespace: str, key: str) -> None: ...


class CheckpointStoreAdapter:
    """Bridge the shared load/save(record) port and the older get/put port."""
    def __init__(self, checkpoints):
        self.checkpoints = checkpoints

    def load(self, namespace, key):
        store = self.checkpoints
        if hasattr(store, "load"):
            return store.load(namespace, key)
        if hasattr(store, "get"):
            return store.get(namespace, key)
        raise TypeError("checkpoint store must provide load/save/delete or get/put/delete")

    def save(self, record):
        store = self.checkpoints
        if hasattr(store, "save"):
            store.save(record)
        elif hasattr(store, "put"):
            store.put(record["namespace"], record["key"], record["payload"])
        else:
            raise TypeError("checkpoint store must provide load/save/delete or get/put/delete")

    def delete(self, namespace, key):
        self.checkpoints.delete(namespace, key)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


class RequestScheduler:
    def __init__(self, *, monotonic=time.monotonic, sleep=time.sleep, checkpoints=None,
                 store=None, wall_clock=time.time):
        if checkpoints is not None and store is not None and checkpoints is not store:
            raise ValueError("pass either checkpoints or store, not both")
        self.monotonic, self.sleep, self.wall_clock = monotonic, sleep, wall_clock
        self._locks = {g: threading.RLock() for g in ("arxiv", "semantic_scholar")}
        self._next = {g: 0. for g in self._locks}
        self._s2 = deque()
        self._s2_429 = 0
        selected = checkpoints if checkpoints is not None else store
        self.checkpoints = CheckpointStoreAdapter(selected) if selected is not None else None
        self.store = selected  # backwards-compatible attribute
        self.persist_failures = 0
        self.restored = False
        if self.checkpoints is not None:
            self._restore()

    def _key(self, group):
        return canonical_checkpoint_key(group)

    def _snapshot(self, group):
        mono, wall = self.monotonic(), self.wall_clock()
        waiting = self._next[group] - mono
        payload = {"group": group, "next_allowed_at": utc_iso(wall + max(0., waiting)),
                   "recent_requests_at": [], "consecutive_429": 0}
        if group == "semantic_scholar":
            payload["consecutive_429"] = self._s2_429
            payload["recent_requests_at"] = [utc_iso(wall - (mono - t))
                                                for t in self._s2 if t > mono - S2_WINDOW_SECONDS]
        return payload

    def _persist(self, group):
        if self.checkpoints is None:
            return
        try:
            key = self._key(group)
            self.checkpoints.save(checkpoint_record(SCHEDULER_NAMESPACE, key, self._snapshot(group)))
        except Exception:
            self.persist_failures += 1
            raise

    def _restore(self):
        mono, wall = self.monotonic(), self.wall_clock()
        for group in self._locks:
            record = self.checkpoints.load(SCHEDULER_NAMESPACE, self._key(group))
            if record is None:
                continue
            if (not isinstance(record, dict) or record.get("format_version") != SCHEDULER_FORMAT_VERSION or
                    record.get("namespace") != SCHEDULER_NAMESPACE or record.get("key") != self._key(group)):
                raise ValueError(f"corrupt rate-limit checkpoint for {group}")
            payload = record.get("payload")
            if not isinstance(payload, dict) or payload.get("group") != group:
                raise ValueError(f"corrupt rate-limit payload for {group}")
            at = parse_utc_iso(payload.get("next_allowed_at"))
            if at is None:
                raise ValueError(f"invalid next_allowed_at in rate-limit checkpoint for {group}")
            self._next[group] = mono + max(0., at - wall)
            self.restored = True
            if group == "semantic_scholar":
                streak = payload.get("consecutive_429")
                stamps = payload.get("recent_requests_at")
                if type(streak) is not int or not 0 <= streak <= 1000 or not isinstance(stamps, list):
                    raise ValueError("corrupt Semantic Scholar rate-limit checkpoint")
                self._s2_429 = streak
                parsed_stamps = [parse_utc_iso(value) for value in stamps]
                if any(value is None for value in parsed_stamps):
                    raise ValueError("invalid recent request timestamp in rate-limit checkpoint")
                ages = sorted(wall - stamp for stamp in parsed_stamps
                              if 0 <= wall - stamp < S2_WINDOW_SECONDS)
                self._s2.extend(mono - age for age in reversed(ages[:S2_WINDOW_REQUESTS]))
                self.restored = self.restored or bool(self._s2)

    @contextmanager
    def request(self, group):
        with self._locks[group]:
            while True:
                now = self.monotonic()
                while self._s2 and self._s2[0] <= now - S2_WINDOW_SECONDS:
                    self._s2.popleft()
                at = self._next[group]
                if group == "semantic_scholar" and len(self._s2) >= S2_WINDOW_REQUESTS:
                    at = max(at, self._s2[0] + S2_WINDOW_SECONDS)
                if at <= now:
                    break
                self.sleep(at - now)

            # Reserve and persist the request slot BEFORE the caller can send a request.
            previous_next = self._next[group]
            request_time = self.monotonic()
            previous_s2 = tuple(self._s2)
            self._next[group] = max(previous_next, request_time + (3 if group == "arxiv" else 1))
            if group == "semantic_scholar":
                self._s2.append(request_time)
            try:
                self._persist(group)
            except Exception:
                self._next[group] = previous_next
                self._s2.clear()
                self._s2.extend(previous_s2)
                raise
            yield

    def defer(self, group, seconds):
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
            raise ValueError("Cooldown must be finite and nonnegative")
        with self._locks[group]:
            # A server-provided Retry-After deadline is authoritative even if writing
            # the checkpoint fails. Keep it in memory so this process cannot retry early.
            self._next[group] = max(self._next[group], self.monotonic() + seconds)
            self._persist(group)

    def rate_limited(self, group, retry_after):
        with self._locks[group]:
            if group == "semantic_scholar":
                self._s2_429 += 1
                retry_after = rate_limit_delay(self._s2_429, retry_after)
            self.defer(group, retry_after)
            return retry_after

    def request_succeeded(self, group):
        with self._locks[group]:
            if group == "semantic_scholar" and self._s2_429:
                previous = self._s2_429
                self._s2_429 = 0
                try:
                    self._persist(group)
                except Exception:
                    self._s2_429 = previous
                    raise


def scheduled_transport(transport, scheduler, group, client):
    """Buffered transports consume and close their response before returning."""
    def send(url, timeout, headers):
        with scheduler.request(group):
            client.attempts += 1
            response = transport(url, timeout, headers)
            if response.status_code == 429:
                scheduler.rate_limited(group, client._retry_after(response.headers))
            elif response.status_code == 200:
                scheduler.request_succeeded(group)
            elif response.status_code in (500, 502, 503, 504):
                scheduler.defer(group, client._retry_after(response.headers))
            return response
    return send
