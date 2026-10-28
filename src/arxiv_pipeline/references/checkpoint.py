"""T4 checkpoint helpers conforming to the shared pipeline CheckpointStore contract."""
import json
import os
import hashlib
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from ..http import CheckpointStoreAdapter, canonical_checkpoint_key, checkpoint_record, utc_iso
from ..runner.checkpoints import normalize_checkpoint
from .api import ARXIV_API, valid_iso_date

FORMAT_VERSION = 1
PAGES_NAMESPACE = "references"
DATES_NAMESPACE = "arxiv_dates"


def _utc_now():
    return utc_iso(__import__("time").time())


def make_record(namespace, key, payload):
    return checkpoint_record(namespace, key, deepcopy(payload))


def _payload_of(record, namespace, key):
    if record is None:
        return None
    if not isinstance(record, dict) or record.get("format_version") != FORMAT_VERSION:
        raise ValueError(f"corrupt checkpoint record in namespace {namespace!r}")
    if record.get("namespace") != namespace or record.get("key") != key:
        raise ValueError(f"checkpoint identity mismatch in namespace {namespace!r}")
    payload = record.get("payload")
    if not isinstance(payload, dict):
        raise ValueError(f"corrupt checkpoint payload in namespace {namespace!r}")
    return payload


class MemoryCheckpointStore:
    """Contract-compatible in-memory store; retains get/put aliases for old tests/callers."""
    def __init__(self):
        self.records, self.puts, self.fail_puts = {}, 0, False

    def load(self, namespace, key):
        record = self.records.get((namespace, key))
        return normalize_checkpoint(deepcopy(record)) if record is not None else None

    def save(self, record):
        if self.fail_puts:
            raise OSError("simulated checkpoint write failure")
        normalized = normalize_checkpoint(record)
        self.puts += 1
        self.records[(normalized["namespace"], normalized["key"])] = deepcopy(normalized)

    def get(self, namespace, key):
        return self.load(namespace, key)

    def put(self, namespace, key, payload):
        self.save(make_record(namespace, key, payload))

    def delete(self, namespace, key):
        self.records.pop((namespace, key), None)


class JsonFileCheckpointStore:
    """One JSON file per (namespace, key), replaced atomically (write temp, fsync, rename)."""
    def __init__(self, root):
        self.root = Path(root)

    def _path(self, namespace, key):
        # Hash the canonical key to avoid collisions caused by replacing '/', quotes, etc.
        safe = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.root / namespace / f"{safe}.json"

    def load(self, namespace, key):
        path = self._path(namespace, key)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        try:
            record = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"corrupt checkpoint JSON: {path}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"corrupt checkpoint record: {path}")
        return normalize_checkpoint(record)

    def save(self, record):
        record = normalize_checkpoint(record)
        path = self._path(record["namespace"], record["key"])
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        except BaseException:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise

    def get(self, namespace, key):
        return self.load(namespace, key)

    def put(self, namespace, key, payload):
        self.save(make_record(namespace, key, payload))

    def delete(self, namespace, key):
        try:
            self._path(namespace, key).unlink()
        except FileNotFoundError:
            pass


class PageCheckpoint:
    """Persist accepted reference pages with their identity and request configuration."""
    def __init__(self, store):
        self.store = CheckpointStoreAdapter(store)

    @staticmethod
    def key(owner, arxiv_id, page_size, fields):
        return canonical_checkpoint_key(owner, arxiv_id, page_size, fields)

    def load(self, owner, arxiv_id, page_size, fields):
        key = self.key(owner, arxiv_id, page_size, fields)
        record = self.store.load(PAGES_NAMESPACE, key)
        if record is None:
            # Do not silently treat an old, under-specified identity as an empty cache.
            legacy_key = canonical_checkpoint_key(arxiv_id)
            legacy = self.store.load(PAGES_NAMESPACE, legacy_key) if legacy_key != key else None
            if legacy is not None:
                raise ValueError("legacy reference checkpoint key is incompatible; remove or migrate it")
        payload = _payload_of(record, PAGES_NAMESPACE, key)
        if payload is None:
            return None
        if (payload.get("owner") != owner or payload.get("arxiv_id") != arxiv_id or
                payload.get("page_size") != page_size or payload.get("fields") != fields):
            raise ValueError("reference checkpoint payload identity does not match its key")
        pages = payload.get("pages")
        if not isinstance(pages, list) or not pages:
            raise ValueError("corrupt reference checkpoint: pages must be a nonempty list")
        expected_offset = 0
        items = []
        for index, page in enumerate(pages):
            if not isinstance(page, dict):
                raise ValueError("corrupt reference checkpoint: page must be an object")
            offset, following = page.get("offset"), page.get("next_offset")
            raw_items, fetched_at = page.get("raw_items"), page.get("fetched_at")
            if type(offset) is not int or offset != expected_offset:
                raise ValueError("corrupt reference checkpoint: invalid page offset chain")
            if not isinstance(raw_items, list) or any(not isinstance(item, dict) for item in raw_items):
                raise ValueError("corrupt reference checkpoint: raw_items must be objects")
            if not isinstance(fetched_at, str) or not fetched_at:
                raise ValueError("corrupt reference checkpoint: missing fetched_at")
            if following is not None and (type(following) is not int or following <= offset):
                raise ValueError("corrupt reference checkpoint: invalid next_offset")
            if index < len(pages) - 1 and following is None:
                raise ValueError("corrupt reference checkpoint: pages follow a terminal page")
            items.extend(deepcopy(raw_items))
            if following is None:
                if index != len(pages) - 1:
                    raise ValueError("corrupt reference checkpoint: terminal page is not last")
            else:
                expected_offset = following
        last_next = pages[-1].get("next_offset")
        return deepcopy(pages), items, last_next, last_next is None, len(pages)

    def save(self, owner, arxiv_id, page_size, fields, pages):
        key = self.key(owner, arxiv_id, page_size, fields)
        payload = {"owner": owner, "arxiv_id": arxiv_id, "page_size": page_size,
                   "fields": fields, "pages": deepcopy(pages)}
        self.store.save(make_record(PAGES_NAMESPACE, key, payload))

    def discard(self, owner, arxiv_id, page_size, fields):
        self.store.delete(PAGES_NAMESPACE, self.key(owner, arxiv_id, page_size, fields))


class DateCache:
    """Cache positive arXiv API v1 submission dates within a specific run scope."""
    def __init__(self, store):
        self.store = CheckpointStoreAdapter(store)

    @staticmethod
    def key(arxiv_id, run_id):
        return canonical_checkpoint_key(run_id, arxiv_id)

    def get_many(self, ids, run_id):
        found = {}
        for arxiv_id in ids:
            key = self.key(arxiv_id, run_id)
            payload = _payload_of(self.store.load(DATES_NAMESPACE, key), DATES_NAMESPACE, key)
            if payload is None:
                continue
            value = payload.get("submission_date")
            if (payload.get("arxiv_id") != arxiv_id or payload.get("source") != ARXIV_API or
                    not valid_iso_date(value) or not isinstance(payload.get("fetched_at"), str)):
                raise ValueError(f"corrupt arXiv date cache entry for {arxiv_id}")
            found[arxiv_id] = value
        return found

    def put_many(self, dates, run_id):
        for arxiv_id, value in dates.items():
            if valid_iso_date(value):
                key = self.key(arxiv_id, run_id)
                payload = {"arxiv_id": arxiv_id, "submission_date": value,
                           "source": ARXIV_API, "fetched_at": _utc_now()}
                self.store.save(make_record(DATES_NAMESPACE, key, payload))
