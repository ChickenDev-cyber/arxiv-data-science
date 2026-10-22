"""HTTP clients and strict parsers for T4 (standard library only).

Two sources, kept separate on purpose:
  * Semantic Scholar Graph API: the list of references of one paper (paged).
  * arXiv API: the arXiv submission date (``published`` of v1) of cited papers.
    Semantic Scholar's own ``publicationDate`` is never used as a substitute.

Both clients satisfy the small protocols in ``service.py`` so a shared
adapter/cache from the runner can replace them without touching the module.
"""

import json
import math
import os
import re
import socket
import threading
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from urllib.error import URLError
from urllib.parse import quote, urlencode, urlsplit
from http.client import HTTPException
from xml.etree import ElementTree as ET

from ..discovery.api import ApiResponse, RemoteError, _default_transport, _positive_number
from ..identifiers import is_valid_arxiv_id as is_canonical_id, normalize_arxiv_id

S2_BASE = "https://api.semanticscholar.org/graph/v1/paper/"
ARXIV_API = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
S2_FIELDS = "title,authors,externalIds"


class _PacedClient:
    """One paced connection with bounded retries; share one instance per host.

    ``attempts`` counts requests actually sent, retries included. Cache or
    offline use sends none. The lock covers requests and retries; other
    processes/machines must still be coordinated outside this class.
    """

    def __init__(self, *, delay, timeout=30, max_attempts=3, headers=None, transport=None,
                 monotonic=time.monotonic, sleep=time.sleep, wall_clock=time.time):
        _positive_number(timeout, "timeout", 0.001)
        _positive_number(delay, "delay", 0)
        if type(max_attempts) is not int or not 1 <= max_attempts <= 3:
            raise ValueError("max_attempts must be an integer from 1 to 3")
        self.timeout, self.delay, self.max_attempts = timeout, delay, max_attempts
        self.headers = dict(headers or {})
        self.transport = transport or _default_transport
        self.monotonic, self.sleep, self.wall_clock = monotonic, sleep, wall_clock
        self.attempts = 0
        self._next_allowed = 0.0
        self._lock = threading.Lock()

    def _retry_after(self, headers):
        """Seconds to wait from ``Retry-After``: delay-seconds or HTTP-date, never negative, not capped."""
        value = next((v for k, v in headers.items() if k.lower() == "retry-after"), "")
        try:
            seconds = float(value)
            return max(0.0, seconds) if math.isfinite(seconds) else 0.0
        except (TypeError, ValueError):
            try:
                date = parsedate_to_datetime(value)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                return max(0.0, date.timestamp() - self.wall_clock())
            except (TypeError, ValueError, OverflowError):
                return 0.0

    def _get(self, url, *, not_found_is_error=True):
        with self._lock:
            for attempt in range(self.max_attempts):
                wait = self._next_allowed - self.monotonic()
                if wait > 0:
                    self.sleep(wait)
                self.attempts += 1
                retry_after = 0.0
                try:
                    response = self.transport(url, self.timeout, self.headers)
                    if response.status_code == 200:
                        return response.body
                    retry_after = self._retry_after(response.headers)
                    code = response.status_code
                    if code == 404 and not_found_is_error:
                        error = RemoteError("not_found", "Resource not found (HTTP 404)", False, 404)
                    else:
                        retryable = code == 429 or code in (500, 502, 503, 504)
                        error = RemoteError("rate_limited" if code == 429 else "remote_error",
                                            f"Server returned HTTP {code}", retryable, code)
                except RemoteError as exc:  # oversize body from the transport
                    error = exc
                except (URLError, TimeoutError, socket.timeout, ConnectionError, HTTPException) as exc:
                    error = RemoteError("network_error", f"Request failed ({type(exc).__name__})", True)
                finally:
                    self._next_allowed = self.monotonic() + self.delay
                self._next_allowed = max(self._next_allowed, self.monotonic() + retry_after)
                if not error.info["retryable"] or attempt + 1 == self.max_attempts:
                    raise error
                self._next_allowed = max(self._next_allowed, self.monotonic() + 3 * (2 ** attempt))
        raise AssertionError("retry loop exhausted unexpectedly")


class SemanticScholarClient(_PacedClient):
    """Reads one page of references: ``references_page(arxiv_id, offset, limit) -> dict``.

    The API key (optional, raises the rate limit) comes from the argument or the
    ``S2_API_KEY`` environment variable and is only ever sent to Semantic Scholar.
    """

    def __init__(self, *, api_key=None, delay=1.0, user_agent="arxiv-data-science/T4 (academic course project)",
                 **kwargs):
        key = api_key if api_key is not None else os.environ.get("S2_API_KEY")
        headers = {"User-Agent": user_agent, "Accept": "application/json"}
        if key:
            headers["x-api-key"] = key
        super().__init__(delay=delay, headers=headers, **kwargs)

    def references_page(self, arxiv_id, offset, limit):
        if not is_canonical_id(arxiv_id):
            raise ValueError("references_page expects a canonical arXiv ID")
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("offset must be >= 0 and limit within 1..1000")
        url = (S2_BASE + "ARXIV:" + quote(arxiv_id, safe="/") + "/references?"
               + urlencode({"fields": S2_FIELDS, "offset": offset, "limit": limit}))
        body = self._get(url)
        if len(body) > MAX_RESPONSE_BYTES:
            raise RemoteError("invalid_payload", "Semantic Scholar response exceeds 16 MiB")
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise RemoteError("invalid_payload", "Semantic Scholar response is not valid JSON") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise RemoteError("invalid_payload", "Semantic Scholar response has no 'data' list")
        return payload


class ArxivDateClient(_PacedClient):
    """Looks up arXiv submission dates: ``submission_dates(ids) -> dict[id, str]``.

    IDs absent from the answer are simply not in the dictionary; this client does
    not claim they do not exist.
    """

    def __init__(self, *, delay=3, user_agent="arxiv-data-science/T4 (academic course project)", **kwargs):
        _positive_number(delay, "delay", 3)
        super().__init__(delay=delay, headers={"User-Agent": user_agent, "Accept": "application/atom+xml"},
                         **kwargs)

    def submission_dates(self, ids):
        if not ids or len(ids) > 100 or not all(is_canonical_id(i) for i in ids):
            raise ValueError("submission_dates requires 1 to 100 canonical arXiv IDs")
        url = ARXIV_API + "?" + urlencode({"id_list": ",".join(ids), "start": 0, "max_results": len(ids)})
        return parse_submission_dates(self._get(url, not_found_is_error=False), ids)


def parse_submission_dates(body, requested_ids):
    """``published`` (= submission time of v1) per requested ID from an Atom feed."""
    if not isinstance(body, bytes) or len(body) > MAX_RESPONSE_BYTES:
        raise RemoteError("invalid_payload", "Invalid response type or size")
    if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise RemoteError("invalid_payload", "XML DTD/entities are not accepted")
    try:
        root = ET.fromstring(body)
    except (ET.ParseError, ValueError):
        raise RemoteError("invalid_payload", "arXiv response is not valid XML") from None
    if root.tag != ATOM + "feed":
        raise RemoteError("invalid_payload", "Expected an Atom feed, received another payload")
    requested, dates = set(requested_ids), {}
    for entry in root.findall(ATOM + "entry"):
        raw_id = entry.findtext(ATOM + "id", "").strip()
        parsed = urlsplit(raw_id)
        if parsed.hostname not in ("arxiv.org", "export.arxiv.org") or not parsed.path.startswith("/abs/"):
            raise RemoteError("invalid_payload", "Atom feed contains an error or invalid entry ID")
        arxiv_id = normalize_arxiv_id(parsed.path[len("/abs/"):])
        if arxiv_id is None or arxiv_id not in requested:
            raise RemoteError("invalid_payload", "Feed returned an ID outside the requested batch")
        published = entry.findtext(ATOM + "published", "").strip()
        if arxiv_id not in dates and valid_iso_date(published):
            dates[arxiv_id] = published
    return dates


def valid_iso_date(value):
    """YYYY-MM-DD, or a timestamp with an explicit timezone (Z or +hh:mm)."""
    from datetime import date, datetime
    if not isinstance(value, str):
        return False
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            date.fromisoformat(value)
            return True
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value):
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return True
    except ValueError:
        return False
    return False
