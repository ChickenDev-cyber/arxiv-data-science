"""arXiv OAI-PMH client and strict arXivRaw parser for T2 (standard library only).

The Atom API used by T1 only exposes the v1 date (published), the latest
version date (updated) and the latest version number, so it cannot prove the
date of every version. arXivRaw lists each version with its own date.
"""

import re
import socket
from datetime import timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode
from xml.etree import ElementTree as ET

from ..discovery.api import MAX_RESPONSE_BYTES, ApiClient, RemoteError

# Check this endpoint in a browser before a real run: arXiv moved OAI-PMH from
# https://export.arxiv.org/oai2 to this host; the old URL may only redirect.
OAI_URL = "https://oaipmh.arxiv.org/oai"
OAI = "{http://www.openarchives.org/OAI/2.0/}"
RAW = "{http://arxiv.org/OAI/arXivRaw/}"


def record_url(arxiv_id):
    return OAI_URL + "?" + urlencode({"verb": "GetRecord", "metadataPrefix": "arXivRaw",
                                      "identifier": f"oai:arXiv.org:{arxiv_id}"})


class OaiClient(ApiClient):
    """Same pacing, retry and Retry-After rules as T1's ApiClient, for one OAI record."""

    def __init__(self, *, user_agent="arxiv-data-science/T2 (academic course project)", **kwargs):
        super().__init__(user_agent=user_agent, **kwargs)

    def record(self, arxiv_id):
        url = record_url(arxiv_id)
        with self._lock:
            for attempt in range(self.max_attempts):
                wait = self._next_allowed - self.monotonic()
                if wait > 0:
                    self.sleep(wait)
                self.attempts += 1
                retry_after = 0.0
                try:
                    response = self.transport(url, self.timeout, {"User-Agent": self.user_agent,
                                                               "Accept": "application/xml"})
                    if response.status_code == 200:
                        return response.body
                    retry_after = self._retry_after(response.headers)
                    # OAI-PMH uses 503 + Retry-After for flow control.
                    retryable = response.status_code == 429 or response.status_code in (500, 502, 503, 504)
                    error = RemoteError("rate_limited" if response.status_code in (429, 503) else "remote_error",
                                        f"arXiv OAI returned HTTP {response.status_code}",
                                        retryable, response.status_code)
                except (URLError, TimeoutError, socket.timeout, ConnectionError, HTTPException) as exc:
                    error = RemoteError("network_error", f"Request failed ({type(exc).__name__})", True)
                finally:
                    self._next_allowed = self.monotonic() + self.delay
                self._next_allowed = max(self._next_allowed, self.monotonic() + retry_after)
                if not error.info["retryable"] or attempt + 1 == self.max_attempts:
                    raise error
                self._next_allowed = max(self._next_allowed, self.monotonic() + 3 * (2 ** attempt))
        raise AssertionError("retry loop exhausted unexpectedly")


class FileClient:
    """Offline client for previews and tests: reads saved arXivRaw responses, sends no request."""

    def __init__(self, paths):
        self.paths = {arxiv_id: Path(path) for arxiv_id, path in paths.items()}
        self.attempts = 0

    def record(self, arxiv_id):
        if arxiv_id not in self.paths:
            raise RemoteError("network_error", f"No saved response for {arxiv_id}", True)
        return self.paths[arxiv_id].read_bytes()


def _text(element, tag):
    value = element.findtext(RAW + tag)
    value = " ".join(value.split()) if value else ""
    return value or None


def _timestamp(value):
    """'Mon, 3 Mar 2025 18:00:00 GMT' -> '2025-03-03T18:00:00Z'; unreadable -> None."""
    if not value or not value.strip():
        return None
    try:
        date = parsedate_to_datetime(value.strip())
    except (ValueError, TypeError, OverflowError):
        return None
    if date.tzinfo is None:
        return None  # do not invent a timezone
    return date.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _split_authors(value):
    """Split 'A, B (Univ X, Lab Y) and C' at top level; drop parenthesised affiliations.

    An absent or blank authors field is unknown (None), never a confirmed empty list.
    """
    if value is None or not value.strip():
        return None
    names, current, depth = [], [], 0
    text = " ".join(value.split())
    index = 0
    while index < len(text):
        char = text[index]
        depth += (char == "(") - (char == ")")
        if depth == 0 and (char == "," or text.startswith(" and ", index)):
            names.append("".join(current))
            current = []
            index += 1 if char == "," else 5
            continue
        current.append(char)
        index += 1
    names.append("".join(current))
    cleaned = [re.sub(r"\s*\([^()]*\)", "", name).strip() for name in names]
    return [name for name in cleaned if name] or None


def parse_record(body, arxiv_id):
    """Return Metadata data for one ID; raise RemoteError for absent or invalid payloads."""
    if not isinstance(body, bytes) or len(body) > MAX_RESPONSE_BYTES:
        raise RemoteError("invalid_payload", "Invalid response type or size")
    if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise RemoteError("invalid_payload", "XML DTD/entities are not accepted")
    # A blank line before <?xml ...?> (common in hand-copied files) is not valid XML; drop it.
    body = body.removeprefix(b"\xef\xbb\xbf").lstrip()
    if not body:
        raise RemoteError("invalid_payload", "arXiv OAI response is empty")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        line, column = exc.position
        raise RemoteError("invalid_payload",
                          f"arXiv OAI response is not valid XML (line {line}, column {column})") from None
    except ValueError:
        raise RemoteError("invalid_payload", "arXiv OAI response is not valid XML") from None
    if root.tag != OAI + "OAI-PMH":
        raise RemoteError("invalid_payload", "Expected an OAI-PMH response")
    error = root.find(OAI + "error")
    if error is not None:
        if error.get("code") == "idDoesNotExist":
            raise RemoteError("not_found", f"arXiv OAI has no record for {arxiv_id}")
        raise RemoteError("remote_error", f"arXiv OAI error: {error.get('code') or 'unknown'}")
    raw = root.find(f"{OAI}GetRecord/{OAI}record/{OAI}metadata/{RAW}arXivRaw")
    if raw is None:
        raise RemoteError("invalid_payload", "OAI record has no arXivRaw metadata")
    if _text(raw, "id") != arxiv_id:
        raise RemoteError("invalid_payload", "OAI record ID does not match the requested ID")

    versions = []
    for element in raw.findall(RAW + "version"):
        match = re.fullmatch(r"v([1-9]\d*)", element.get("version", ""))
        if not match:
            raise RemoteError("invalid_payload", "OAI record has an invalid version label")
        versions.append({"version": int(match.group(1)), "submitted_at": _timestamp(element.findtext(RAW + "date"))})
    numbers = [v["version"] for v in versions]
    if len(set(numbers)) != len(numbers):
        raise RemoteError("invalid_payload", "OAI record repeats a version")
    versions.sort(key=lambda v: v["version"])

    first = next((v for v in versions if v["version"] == 1), None)
    contiguous = bool(versions) and sorted(numbers) == list(range(1, len(numbers) + 1))
    return {
        "arxiv_id": arxiv_id,
        "title": _text(raw, "title"),
        "authors": _split_authors(raw.findtext(RAW + "authors")),
        "submission_date": first["submitted_at"] if first else None,
        "revised_dates": [v["submitted_at"] for v in versions if v["version"] >= 2 and v["submitted_at"]],
        "venue": _text(raw, "journal-ref"),
        "versions": versions,
        "versions_complete": contiguous and all(v["submitted_at"] for v in versions),
    }

