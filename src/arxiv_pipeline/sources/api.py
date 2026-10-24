"""T3 arXiv source transport: streaming downloader and SourceClient protocol."""

from datetime import datetime, timezone
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
        self._lock = threading.Lock()

    def provenance(self, arxiv_id: str, version: int) -> dict[str, str]:
        return {
            "url": f"https://export.arxiv.org/src/{arxiv_id}v{version}",
            "method": "GET",
        }

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
            last_error: RemoteError | None = None
            for attempt in range(1, self.max_attempts + 1):
                wait = self._next_allowed - self.monotonic()
                if wait > 0:
                    self.sleep(wait)

                self.attempts += 1
                self._next_allowed = self.monotonic() + self.delay

                resp = None
                try:
                    resp = self.opener(req, timeout=self.timeout)
                    written = 0
                    with open(destination, "wb") as f_out:
                        while chunk := resp.read(65536):
                            written += len(chunk)
                            if written > self.max_download_bytes:
                                raise RemoteError(
                                    "remote_error",
                                    f"Download size {written} exceeds limit {self.max_download_bytes}",
                                    retryable=False,
                                )
                            f_out.write(chunk)
                    return written

                except RemoteError as exc:
                    destination.unlink(missing_ok=True)
                    if not exc.info.get("retryable", False) or attempt >= self.max_attempts:
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
                        if attempt >= self.max_attempts:
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
                    if not is_retryable or attempt >= self.max_attempts:
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
                    if attempt >= self.max_attempts:
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

                finally:
                    if resp is not None and hasattr(resp, "close"):
                        resp.close()

            if last_error is not None:
                raise last_error
            raise RemoteError("remote_error", "Failed to download after retries exhausted", retryable=False)
