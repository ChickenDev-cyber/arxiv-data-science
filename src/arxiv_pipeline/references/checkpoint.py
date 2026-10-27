"""T4 checkpoint helpers conforming to the shared pipeline CheckpointStore contract."""
import json
import os
import hashlib
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from ..http import CheckpointStoreAdapter, canonical_checkpoint_key, checkpoint_record, utc_iso
from .api import ARXIV_API, valid_iso_date

FORMAT_VERSION = 1
PAGES_NAMESPACE = "references"
DATES_NAMESPACE = "arxiv_dates"


def _utc_now():
    return utc_iso(__import__("time").time())


def make_record(namespace, key, payload):
    return checkpoint_record(namespace, key, deepcopy(payload))


def _payload_of(record, namespace, key):
    if not isinstance(record, dict) or record.get("format_version") != FORMAT_VERSION:
        return None
    if record.get("namespace") != namespace or record.get("key") != key:
        return None
    payload = record.get("payload")
    return payload if isinstance(payload, dict) else None


class MemoryCheckpointStore:
    """Contract-compatible in-memory store; retains get/put aliases for old tests/callers."""
    def __init__(self):
        self.records, self.puts, self.fail_puts = {}, 0, False

    def load(self, namespace, key):
        record = self.records.get((namespace, key))
        return deepcopy(record) if record is not None else None

    def save(self, record):
        if self.fail_puts:
            raise OSError("simulated checkpoint write failure")
        self.puts += 1
        self.records[(record["namespace"], record["key"])] = deepcopy(record)

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
        try:
            record = json.loads(self._path(namespace, key).read_text(encoding="utf-8"))
            return record if isinstance(record, dict) else None
        except (OSError, ValueError):
            return None

    def save(self, record):
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
    def key(arxiv_id):
        return canonical_checkpoint_key(arxiv_id)

    def load(self, owner, arxiv_id, page_size, fields):
        key = self.key(arxiv_id)
        payload = _payload_of(self.store.load(PAGES_NAMESPACE, key), PAGES_NAMESPACE, key)
        if payload is None:
            return None
        if (payload.get("owner") != owner or payload.get("arxiv_id") != arxiv_id or
                payload.get("page_size") != page_size or payload.get("fields") != fields):
            return None
        pages = payload.get("pages")
        if not isinstance(pages, list) or not pages:
            return None
        expected_offset = 0
        items = []
        for index, page in enumerate(pages):
            if not isinstance(page, dict):
                return None
            offset, following = page.get("offset"), page.get("next_offset")
            raw_items, fetched_at = page.get("raw_items"), page.get("fetched_at")
            if type(offset) is not int or offset != expected_offset:
                return None
            if not isinstance(raw_items, list) or any(not isinstance(item, dict) for item in raw_items):
                return None
            if not isinstance(fetched_at, str) or not fetched_at:
                return None
            if following is not None and (type(following) is not int or following <= offset):
                return None
            if index < len(pages) - 1 and following is None:
                return None
            items.extend(deepcopy(raw_items))
            if following is None:
                if index != len(pages) - 1:
                    return None
            else:
                expected_offset = following
        last_next = pages[-1].get("next_offset")
        return deepcopy(pages), items, last_next, last_next is None, len(pages)

    def save(self, owner, arxiv_id, page_size, fields, pages):
        key = self.key(arxiv_id)
        payload = {"owner": owner, "arxiv_id": arxiv_id, "page_size": page_size,
                   "fields": fields, "pages": deepcopy(pages)}
        self.store.save(make_record(PAGES_NAMESPACE, key, payload))

    def discard(self, arxiv_id):
        self.store.delete(PAGES_NAMESPACE, self.key(arxiv_id))


class DateCache:
    """Cache positive arXiv API v1 submission dates in the shared checkpoint format."""
    def __init__(self, store):
        self.store = CheckpointStoreAdapter(store)

    @staticmethod
    def key(arxiv_id):
        return canonical_checkpoint_key(arxiv_id)

    def get_many(self, ids):
        found = {}
        for arxiv_id in ids:
            key = self.key(arxiv_id)
            payload = _payload_of(self.store.load(DATES_NAMESPACE, key), DATES_NAMESPACE, key)
            value = payload.get("submission_date") if payload else None
            if (payload and payload.get("arxiv_id") == arxiv_id and
                    payload.get("source") == ARXIV_API and valid_iso_date(value)):
                found[arxiv_id] = value
        return found

    def put_many(self, dates):
        for arxiv_id, value in dates.items():
            if valid_iso_date(value):
                key = self.key(arxiv_id)
                payload = {"arxiv_id": arxiv_id, "submission_date": value,
                           "source": ARXIV_API, "fetched_at": _utc_now()}
                self.store.save(make_record(DATES_NAMESPACE, key, payload))
