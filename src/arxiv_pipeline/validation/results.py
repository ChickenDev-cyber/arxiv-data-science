"""Structural validation followed by step-specific semantic validation."""
from datetime import datetime, timezone
import json
import math
import re
import types
from typing import get_args, get_origin, get_type_hints, Literal, Union
from ..contracts import Result, SCHEMA_VERSION
from ..identifiers import is_valid_arxiv_id


def issue(result, field, message):
    return {"owner": result.get("owner", "trong"), "arxiv_id": result.get("arxiv_id"),
            "version": result.get("version"), "step": result.get("step", "discovery"),
            "field": field, "message": message}


def pointer(token):
    return str(token).replace("~", "~0").replace("/", "~1")


def _check(value, hint, path):
    origin, args = get_origin(hint), get_args(hint)
    if hint is object:
        return []
    if origin in (Union, types.UnionType):
        return [] if any(not _check(value, h, path) for h in args) else [path]
    if origin is Literal:
        return [] if any(type(value) is type(a) and value == a for a in args) else [path]
    if hasattr(hint, "__required_keys__"):
        if not isinstance(value, dict):
            return [path]
        hints = get_type_hints(hint)
        errors = [path + "/" + pointer(k) for k in set(value) ^ set(hints)]
        for k in set(value) & set(hints):
            errors += _check(value[k], hints[k], path + "/" + pointer(k))
        return errors
    if origin is list:
        return [path] if type(value) is not list else [p for i, v in enumerate(value) for p in _check(v, args[0], f"{path}/{i}")]
    if origin is dict:
        if type(value) is not dict:
            return [path]
        return [p for k, v in value.items() for p in (_check(k, args[0], path) + _check(v, args[1], path + "/" + pointer(k)))]
    if hint in (int, float):
        valid = type(value) is int if hint is int else type(value) in (int, float)
        return [] if valid and math.isfinite(value) else [path]
    return [] if type(value) is hint else [path]


def utc(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset() != timezone.utc.utcoffset(dt):
        raise ValueError("Expected UTC timestamp")
    return dt


def validate_result(result):
    if not isinstance(result, dict):
        return [issue({}, None, "Result must be an object")]
    paths = _check(result, Result, "")
    errors = [issue(result, p or None, "Wrong type or missing/unrecognized field") for p in sorted(set(paths))]
    if errors:
        return errors
    def add(field, message):
        errors.append(issue(result, field, message))
    try:
        json.dumps(result, allow_nan=False)
    except (ValueError, TypeError):
        add(None, "Result must contain finite JSON values")
    if result["schema_version"] != SCHEMA_VERSION:
        add("/schema_version", "Unsupported schema version")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", result["run_id"]):
        add("/run_id", "Invalid run ID")
    step, status, data = result["step"], result["status"], result["data"]
    allowed = {"discovery": {"success", "partial", "failed"},
               "metadata": {"success", "partial", "failed", "not_found"},
               "sources": {"success", "partial", "failed", "not_found", "unsupported"},
               "references": {"success", "partial", "failed", "not_found", "empty"}}
    if status not in allowed[step]:
        add("/status", "Status not allowed for step")
    if (step == "discovery" and result["arxiv_id"] is not None) or (step != "discovery" and not is_valid_arxiv_id(result["arxiv_id"])):
        add("/arxiv_id", "Wrong subject ID")
    if (step == "sources" and (result["version"] is None or result["version"] < 1)) or (step != "sources" and result["version"] is not None):
        add("/version", "Only sources has a positive version")
    if result["attempts"] < 0 or result["duration_seconds"] < 0:
        add("/attempts" if result["attempts"] < 0 else "/duration_seconds", "Negative measurement")
    try:
        if utc(result["finished_at"]) < utc(result["started_at"]):
            raise ValueError("Log times reversed")
    except ValueError as exc:
        add("/finished_at", str(exc))
    error = result["error"]
    if error is not None and (not error["message"].strip() or (error["http_status"] is not None and not 100 <= error["http_status"] <= 599)):
        add("/error", "Invalid error message/status")
    if status in ("failed", "not_found"):
        if data is not None or error is None:
            add("/data", "Failure requires data=null and an error")
    elif data is None:
        add("/data", "Outcome requires useful data")
    if status in ("success", "empty", "unsupported") and error is not None:
        add("/error", "Complete outcome cannot carry an error")
    if status in ("success", "empty", "unsupported") and result["missing_fields"]:
        add("/missing_fields", "Complete outcome cannot have missing fields")
    for p in result["missing_fields"]:
        try:
            if not p.startswith("/") or re.search(r"~(?![01])", p):
                raise ValueError()
            value = result
            for token in p[1:].split("/"):
                token = token.replace("~1", "/").replace("~0", "~")
                value = value[int(token)] if isinstance(value, list) and re.fullmatch(r"0|[1-9]\d*", token) else value[token]
        except (ValueError, KeyError, IndexError, TypeError):
            add("/missing_fields", "JSON Pointer does not resolve")
    from .payloads import validate_payload
    errors += validate_payload(result)
    return errors


def validate_for_task(result, spec, context):
    errors = validate_result(result)
    if not isinstance(result, dict):
        return errors
    for field in ("owner", "step", "arxiv_id", "version"):
        if result.get(field) != getattr(spec, field):
            errors.append(issue(result, "/" + field, "Result does not match assigned task"))
    for field in ("run_id", "is_mock"):
        if type(result.get(field)) is not type(context[field]) or result.get(field) != context[field]:
            errors.append(issue(result, "/" + field, "Result does not match run"))
    return errors
