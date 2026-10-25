"""Small arXiv client and strict Atom parser for T1 (standard library only)."""

import math
import re
import socket
import threading
import time
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET
from ..http import scheduled_transport

API_URL = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
OPEN_SEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"
MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class RemoteError(Exception):
    """An expected remote/data error; programming errors are not swallowed."""

    def __init__(self, code, message, retryable=False, http_status=None):
        super().__init__(message)
        self.info = dict(code=code, message=message, retryable=retryable, http_status=http_status)


@dataclass(frozen=True)
class ApiResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True)
class FeedResult:
    found: frozenset[str]
    complete: bool


def _default_transport(url, timeout, headers):
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise RemoteError("invalid_payload", "API response exceeds 16 MiB")
            return ApiResponse(response.status, dict(response.headers), body)
    except HTTPError as error:
        # HTTPError is also a file-like response; close it without logging its body.
        with error:
            return ApiResponse(error.code, dict(error.headers), b"")


def _positive_number(value, name, minimum):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < minimum):
        raise ValueError(f"{name} must be a finite number >= {minimum}")


class ApiClient:
    """One paced connection per client; share this instance across T1 calls.

    The lock covers requests and retries. Other processes/machines still need
    coordination; creating more client instances does not grant extra quota.
    """

    def __init__(self, *, timeout=30, max_attempts=3, delay=3,
                 user_agent="arxiv-data-science/T1 (academic course project)",
                 transport=None, monotonic=time.monotonic, sleep=time.sleep,
                 wall_clock=time.time, scheduler=None):
        _positive_number(timeout, "timeout", 0.001)
        _positive_number(delay, "delay", 3)
        if type(max_attempts) is not int or not 1 <= max_attempts <= 3:
            raise ValueError("max_attempts must be an integer from 1 to 3")
        if not isinstance(user_agent, str) or not user_agent.strip() or "\n" in user_agent or "\r" in user_agent:
            raise ValueError("user_agent must be a nonempty single-line string")
        self.timeout, self.max_attempts, self.delay = timeout, max_attempts, delay
        self.user_agent = user_agent
        self.transport = transport or _default_transport
        self.monotonic, self.sleep, self.wall_clock = monotonic, sleep, wall_clock
        self.attempts = 0
        self._next_allowed = 0.0
        self._lock = threading.Lock()
        self.scheduler = scheduler
        if scheduler is not None:
            self.transport = scheduled_transport(self.transport, scheduler, "arxiv", self)

    def _retry_after(self, headers):
        value = next((v for k, v in headers.items() if k.lower() == "retry-after"), "")
        try:
            seconds = float(value)
            return max(0.0, seconds) if math.isfinite(seconds) else 0.0
        except (ValueError, TypeError):
            try:
                date = parsedate_to_datetime(value)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                return max(0.0, date.timestamp() - self.wall_clock())
            except (ValueError, TypeError, OverflowError):
                return 0.0

    def query(self, ids):
        if not ids or len(ids) > 100:
            raise ValueError("query requires 1 to 100 IDs")
        for arxiv_id in ids:
            if not re.fullmatch(r"\d{4}\.\d{5}", arxiv_id):
                raise ValueError("query expects canonical modern IDs")
        url = API_URL + "?" + urlencode({"id_list": ",".join(ids), "start": 0, "max_results": len(ids)})
        with self._lock:
            for attempt in range(self.max_attempts):
                wait = self._next_allowed - self.monotonic()
                if wait > 0:
                    self.sleep(wait)
                if self.scheduler is None:
                    self.attempts += 1
                retry_after = 0.0
                try:
                    response = self.transport(url, self.timeout, {"User-Agent": self.user_agent,
                                                               "Accept": "application/atom+xml"})
                    if response.status_code == 200:
                        return response.body
                    retry_after = self._retry_after(response.headers)
                    retryable = response.status_code == 429 or response.status_code in (500, 502, 503, 504)
                    error = RemoteError("rate_limited" if response.status_code == 429 else "remote_error",
                                        f"arXiv returned HTTP {response.status_code}",
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


def _canonical_entry_id(value):
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        raise RemoteError("invalid_payload", "Malformed Atom entry URL") from None
    if parsed.scheme not in ("http", "https") or parsed.hostname not in ("arxiv.org", "export.arxiv.org"):
        raise RemoteError("invalid_payload", "Unexpected Atom entry ID host")
    match = re.fullmatch(r"/abs/(\d{4}\.\d{5})(?:v[1-9]\d*)?", parsed.path)
    if not match or parsed.query or parsed.fragment:
        raise RemoteError("invalid_payload", "Atom feed contains an error or invalid entry ID")
    return match.group(1)


def parse_feed(body, requested_ids):
    """Validate before inferring that a requested ID is absent.

    Without trustworthy pagination metadata the feed may prove presence, but
    cannot prove absence. Unexpected IDs/error entries invalidate the feed.
    """
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
    entries = root.findall(ATOM + "entry")
    requested = set(requested_ids)
    found = set()
    for entry in entries:
        arxiv_id = _canonical_entry_id(entry.findtext(ATOM + "id", ""))
        if arxiv_id not in requested:
            raise RemoteError("invalid_payload", "Feed returned an ID outside the requested batch")
        found.add(arxiv_id)
    try:
        total = int(root.findtext(OPEN_SEARCH + "totalResults", ""))
        start = int(root.findtext(OPEN_SEARCH + "startIndex", ""))
        complete = start == 0 and total >= 0 and total == len(entries)
    except ValueError:
        complete = False
    return FeedResult(frozenset(found), complete)
