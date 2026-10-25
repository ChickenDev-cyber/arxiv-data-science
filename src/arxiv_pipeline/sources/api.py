"""T3 arXiv source transport: streaming downloader and SourceClient protocol."""

from datetime import datetime, timezone
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from http.client import HTTPException
import math
from pathlib import Path
import threading
import time
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..discovery.api import RemoteError
from ..identifiers import is_valid_arxiv_id

DEFAULT_USER_AGENT = "arxiv-pipeline/1.0 (+https://github.com/ChickenDev-cyber/arxiv-data-science)"
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_DELAY = 3.0
DEFAULT_MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024  # 200 MiB


@dataclass
class _FetchBudget:
    remaining: int


class SourceClient(Protocol):
    attempts: int

    def download(self, arxiv_id: str, version: int, destination: Path) -> int: ...

    def provenance(self, arxiv_id: str, version: int) -> dict[str, str]: ...


class ArxivSourceClient:
    """Streamed arXiv source client with retry, rate-limiting, and bounded download sizes."""

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        delay: float = DEFAULT_DELAY,
        user_agent: str = DEFAULT_USER_AGENT,
        max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
        opener=None,
        monotonic=None,
        wall_clock=None,
        sleep=None,
        scheduler=None,
    ) -> None:
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.delay = delay
        self.user_agent = user_agent
        self.max_download_bytes = max_download_bytes
        self.opener = opener.open if opener is not None else urlopen
        self.monotonic = monotonic or time.monotonic
        self.wall_clock = wall_clock or time.time
        self.sleep = sleep or time.sleep
        self.attempts = 0
        self._next_allowed = 0.0
        self._lock = threading.RLock()
        self.scheduler = scheduler
        self._last_response: dict[str, str] = {}
        self._fetch_budget: _FetchBudget | None = None

    @contextmanager
    def fetch_budget(self):
        """Share at most three sends across downloading and extraction retries."""
        with self._lock:
            if self._fetch_budget is not None:
                raise RuntimeError("A source fetch budget is already active")
            budget = _FetchBudget(min(3, self.max_attempts))
            self._fetch_budget = budget
            try:
                yield budget
            finally:
                self._fetch_budget = None

    @contextmanager
    def _response(self, req):
        gate = self.scheduler.request("arxiv") if self.scheduler is not None else nullcontext()
        with gate:
            response = None
            try:
                if self._fetch_budget is not None:
                    if self._fetch_budget.remaining <= 0:
                        raise RemoteError("network_error", "Source fetch attempts exhausted", False)
                    self._fetch_budget.remaining -= 1
                self.attempts += 1
                response = self.opener(req, timeout=self.timeout)
                yield response
            except HTTPError as exc:
                if self.scheduler is not None and exc.code in (429, 500, 502, 503, 504):
                    self.scheduler.defer("arxiv", self._retry_after(exc.headers))
                raise
            finally:
                if response is not None and hasattr(response, "close"):
                    response.close()

    def provenance(self, arxiv_id: str, version: int) -> dict[str, str]:
        return {
            "url": f"https://export.arxiv.org/src/{arxiv_id}v{version}",
            "method": "GET",
            **self._last_response,
        }

    def _inspect_response(self, response) -> int | None:
        """Require a whole response; old offline stream adapters have no HTTP metadata."""
        status = getattr(response, "status", None)
        if status is None:
            getcode = getattr(response, "getcode", None)
            status = getcode() if getcode is not None else 200
        headers = getattr(response, "headers", {}) or {}

        def values(name):
            return [str(v).strip() for k, v in headers.items() if k.lower() == name]

        lengths, content_range = values("content-length"), ", ".join(values("content-range"))
        self._last_response = {"http_status": str(status), "received_bytes": "0"}
        if lengths:
            self._last_response["content_length"] = ", ".join(lengths)[:256]
        if content_range:
            self._last_response["content_range"] = content_range
        if status == 206 or content_range:
            raise RemoteError("network_error", "Partial response to a full source GET", True, status)
        if status != 200:
            raise RemoteError("remote_error", f"Unexpected source HTTP status {status}", False, status)
        if not lengths:
            return None
        parts = [part.strip() for value in lengths for part in value.split(",")]
        if any(not value.isascii() or not value.isdecimal() for value in parts):
            raise RemoteError("invalid_payload", "Invalid source Content-Length", False)
        normalized = {value.lstrip("0") or "0" for value in parts}
        if len(normalized) != 1:
            raise RemoteError("invalid_payload", "Conflicting source Content-Length values", False)
        length = normalized.pop()
        maximum = str(self.max_download_bytes)
        if len(length) > len(maximum) or (len(length) == len(maximum) and length > maximum):
            raise RemoteError("remote_error", "Content-Length exceeds download size limit", False)
        self._last_response["content_length"] = length
        return int(length)

    def _retry_after(self, headers) -> float:
        """Parse Retry-After header: delay-seconds or HTTP-date."""
        if not headers:
            return 0.0
        value = ""
        if hasattr(headers, "get"):
            value = headers.get("Retry-After", "")
        if not value and hasattr(headers, "items"):
            value = next((v for k, v in headers.items() if k.lower() == "retry-after"), "")
        if not value:
            return 0.0

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

    def download(self, arxiv_id: str, version: int, destination: Path) -> int:
        if not is_valid_arxiv_id(arxiv_id):
            raise ValueError(f"Invalid canonical arXiv ID: {arxiv_id}")
        if type(version) is not int or version < 1:
            raise ValueError(f"Version must be a positive integer >= 1: {version}")

        url = f"https://export.arxiv.org/src/{arxiv_id}v{version}"
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)

        req = Request(url, headers={"User-Agent": self.user_agent})

        with self._lock:
            self._last_response = {}
            last_error: RemoteError | None = None
            attempt_limit = self.max_attempts
            if self._fetch_budget is not None:
                attempt_limit = min(attempt_limit, self._fetch_budget.remaining)
            for attempt in range(1, attempt_limit + 1):
                wait = self._next_allowed - self.monotonic()
                if wait > 0:
                    self.sleep(wait)

                self._next_allowed = self.monotonic() + self.delay

                resp = None
                try:
                    with self._response(req) as resp:
                        expected = self._inspect_response(resp)
                        written = 0
                        with open(destination, "wb") as f_out:
                            while chunk := resp.read(65536):
                                written += len(chunk)
                                self._last_response["received_bytes"] = str(written)
                                if written > self.max_download_bytes:
                                    raise RemoteError(
                                        "remote_error",
                                        f"Download size {written} exceeds limit {self.max_download_bytes}",
                                        retryable=False,
                                    )
                                f_out.write(chunk)
                        if expected is not None and written != expected:
                            raise RemoteError(
                                "network_error",
                                f"Incomplete source response: expected {expected} bytes, received {written}",
                                True,
                            )
                        if written == 0:
                            raise RemoteError("invalid_payload", "Empty source response", True)
                        return written

                except RemoteError as exc:
                    destination.unlink(missing_ok=True)
                    if not exc.info.get("retryable", False) or attempt >= attempt_limit:
                        raise
                    last_error = exc
                    self.sleep(self.delay * attempt)

                except HTTPError as exc:
                    destination.unlink(missing_ok=True)
                    retry_after = self._retry_after(getattr(exc, "headers", None))
                    status = exc.code
                    exc.close()

                    if status in (429, 500, 502, 503, 504):
                        # Server cooldown survives exhaustion of this task's retry budget.
                        self._next_allowed = max(
                            self._next_allowed, self.monotonic() + retry_after,
                        )

                    if status == 404:
                        raise RemoteError(
                            "not_found",
                            f"Source version not found (HTTP 404): {url}",
                            retryable=False,
                            http_status=404,
                        )
                    if status == 400:
                        raise RemoteError(
                            "remote_error",
                            f"Bad request (HTTP 400): {url}",
                            retryable=False,
                            http_status=400,
                        )
                    if status == 429:
                        wait_time = max(retry_after, self.delay)
                        self._next_allowed = self.monotonic() + wait_time
                        if attempt >= attempt_limit:
                            raise RemoteError(
                                "rate_limited",
                                f"Rate limited by arXiv (HTTP 429), retries exhausted",
                                retryable=False,
                                http_status=429,
                            )
                        self.sleep(wait_time)
                        continue

                    # 5xx or transient errors
                    is_retryable = status in (500, 502, 503, 504)
                    if not is_retryable or attempt >= attempt_limit:
                        raise RemoteError(
                            "remote_error",
                            f"HTTP {status} error downloading source",
                            retryable=False,
                            http_status=status,
                        )
                    last_error = RemoteError(
                        "remote_error",
                        f"HTTP {status} error downloading source",
                        retryable=True,
                        http_status=status,
                    )
                    self.sleep(max(retry_after, self.delay * attempt))

                except (URLError, TimeoutError, OSError, HTTPException) as exc:
                    destination.unlink(missing_ok=True)
                    if attempt >= attempt_limit:
                        raise RemoteError(
                            "network_error",
                            f"Network error downloading source: {exc}",
                            retryable=False,
                        )
                    last_error = RemoteError(
                        "network_error",
                        f"Network error downloading source: {exc}",
                        retryable=True,
                    )
                    self.sleep(self.delay * attempt)

            if last_error is not None:
                raise last_error
            raise RemoteError("remote_error", "Failed to download after retries exhausted", retryable=False)
