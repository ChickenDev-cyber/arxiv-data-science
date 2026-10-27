"""Storage adapters for operational checkpoints, independent of domain logic."""

from typing import Callable, TYPE_CHECKING, cast, get_args
from copy import deepcopy
import json

from ..pipeline_contracts import CheckpointNamespace, CheckpointRecord
from ..validation.results import utc
from .models import canonical

if TYPE_CHECKING:
    from .state import StateStore


CheckpointValidator = Callable[[CheckpointRecord], list[str]]


def validate_checkpoint_key(namespace: str, key: str) -> None:
    """Reject malformed lookup identities even when no matching record exists."""
    if namespace not in get_args(CheckpointNamespace) or not isinstance(key, str):
        raise ValueError("Invalid checkpoint namespace or key")
    try:
        parts = json.loads(key)
        if not isinstance(parts, list) or not parts or canonical(parts) != key:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("Checkpoint key must be a nonempty canonical JSON array") from None


def normalize_checkpoint(record: object, validator: CheckpointValidator | None = None) -> CheckpointRecord:
    """Check storage envelope and copy JSON; payload semantics belong to validator."""
    fields = {"format_version", "namespace", "key", "payload", "updated_at"}
    if not isinstance(record, dict) or set(record) != fields:
        raise ValueError("Malformed checkpoint envelope")
    if type(record["format_version"]) is not int or record["format_version"] != 1:
        raise ValueError("Unsupported checkpoint format_version")
    validate_checkpoint_key(record["namespace"], record["key"])
    if not isinstance(record["payload"], dict) or not isinstance(record["updated_at"], str):
        raise ValueError("Checkpoint payload must be an object with a UTC updated_at")
    utc(record["updated_at"])
    try:
        snapshot = json.loads(canonical(record))
    except (TypeError, ValueError) as exc:
        raise ValueError("Checkpoint must contain finite JSON values") from exc
    if snapshot != record:
        raise ValueError("Checkpoint must round-trip through JSON without conversion")
    result = cast(CheckpointRecord, snapshot)
    if validator is not None:
        errors = validator(deepcopy(result))
        if not isinstance(errors, list) or any(not isinstance(e, str) for e in errors):
            raise TypeError("Checkpoint validator must return list[str]")
        if errors:
            raise ValueError("Invalid checkpoint: " + "; ".join(errors))
    return result


class SQLiteCheckpointStore:
    def __init__(self, store: "StateStore", *, validate_record: CheckpointValidator | None = None):
        self.store = store
        self.validate_record = validate_record

    def load(self, namespace: str, key: str) -> CheckpointRecord | None:
        record = self.store.load_checkpoint(namespace, key)
        return None if record is None else normalize_checkpoint(record, self.validate_record)

    def save(self, record: CheckpointRecord) -> None:
        self.store.save_checkpoint(normalize_checkpoint(record, self.validate_record))

    def delete(self, namespace: str, key: str) -> None:
        self.store.delete_checkpoint(namespace, key)


class InMemoryCheckpointStore:
    def __init__(self, *, validate_record: CheckpointValidator | None = None):
        self.validate_record = validate_record
        self._records: dict[tuple[str, str], CheckpointRecord] = {}

    def load(self, namespace: str, key: str) -> CheckpointRecord | None:
        validate_checkpoint_key(namespace, key)
        record = self._records.get((namespace, key))
        return None if record is None else normalize_checkpoint(record, self.validate_record)

    def save(self, record: CheckpointRecord) -> None:
        saved = normalize_checkpoint(record, self.validate_record)
        self._records[saved["namespace"], saved["key"]] = saved

    def delete(self, namespace: str, key: str) -> None:
        validate_checkpoint_key(namespace, key)
        self._records.pop((namespace, key), None)
