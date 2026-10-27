"""Canonical JSON shared by task identities and durable checkpoints."""

import json


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
