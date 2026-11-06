"""Semantic Scholar Batch API client (POST /graph/v1/paper/batch).

Standard library only, secure headers, adaptive batch sizing.
"""
import http.client
import json
import math
import os
import threading
import time
from urllib.error import URLError
from urllib.parse import urlencode, urlsplit

from ..discovery.api import ApiResponse, RemoteError
from ..identifiers import is_valid_arxiv_id, normalize_arxiv_id

S2_BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"
S2_BATCH_FIELDS = "title,authors,externalIds,referenceCount,references.title,references.authors,references.externalIds"
MAX_BATCH_RESPONSE_BYTES = 32 * 1024 * 1024


def adapt_batch_reference(ref_dict: dict) -> dict:
    """Wrap raw reference object in citedPaper wrapper to match Schema 1.2 raw_references."""
    return {"citedPaper": ref_dict}


def _default_post_transport(url: str, timeout: float, headers: dict, body: bytes | None = None) -> ApiResponse:
    target = urlsplit(url)
    if target.scheme != "https" or target.hostname != "api.semanticscholar.org":
        raise ValueError("Semantic Scholar transport requires HTTPS api.semanticscholar.org")
    connection = http.client.HTTPSConnection("api.semanticscholar.org", timeout=timeout)
    try:
        path = (target.path or "/") + ("?" + target.query if target.query else "")
        connection.request("POST", path, body=body, headers=headers)
        with connection.getresponse() as response:
            resp_body = b""
            if response.status in (200, 400, 429, 500, 502, 503, 504):
                resp_body = response.read(MAX_BATCH_RESPONSE_BYTES + 1)
                if len(resp_body) > MAX_BATCH_RESPONSE_BYTES:
                    raise RemoteError("invalid_payload", "Batch response exceeds size limit")
            return ApiResponse(response.status, dict(response.getheaders()), resp_body)
    except OSError:
        raise URLError("Semantic Scholar HTTPS POST request failed") from None
    finally:
        connection.close()


class BatchSemanticScholarClient:
    """Client for querying Semantic Scholar paper/batch endpoint."""

    def __init__(self, *, api_key: str | None = None, delay: float = 1.0,
                 timeout: float = 45.0, user_agent: str = "arxiv-data-science/T4-batch",
                 transport=None, monotonic=time.monotonic, sleep=time.sleep):
        key = api_key if api_key is not None else os.environ.get("S2_API_KEY")
        if key is not None and (not isinstance(key, str) or any(ord(c) < 33 or ord(c) > 126 for c in key)):
            raise ValueError("API key must be printable ASCII without whitespace")
        self.api_key = key
        self.delay = max(0.0, float(delay))
        self.timeout = max(1.0, float(timeout))
        self.transport = transport or _default_post_transport
        self.monotonic, self.sleep = monotonic, sleep
        self._next_allowed = 0.0
        self._lock = threading.Lock()
        self.headers = {
            "User-Agent": user_agent,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if key:
            self.headers["x-api-key"] = key

    def _retry_after(self, headers: dict) -> float:
        val = next((v for k, v in headers.items() if k.lower() == "retry-after"), "")
        try:
            sec = float(val)
            return max(0.0, sec) if math.isfinite(sec) else 0.0
        except (ValueError, TypeError):
            return 30.0

    def fetch_batch(self, arxiv_ids: list[str]) -> list[dict | None]:
        """Fetch references for up to 100 arXiv IDs in a single POST request."""
        if not arxiv_ids:
            return []
        if len(arxiv_ids) > 500:
            raise ValueError("Single batch cannot exceed 500 IDs")

        body_dict = {"ids": [f"ARXIV:{aid}" for aid in arxiv_ids]}
        encoded_body = json.dumps(body_dict).encode("utf-8")
        query_url = f"{S2_BATCH_URL}?{urlencode({'fields': S2_BATCH_FIELDS})}"

        with self._lock:
            max_attempts = 5
            for attempt in range(max_attempts):
                wait = self._next_allowed - self.monotonic()
                if wait > 0:
                    self.sleep(wait)

                try:
                    resp = self.transport(query_url, self.timeout, self.headers, encoded_body)
                except Exception as exc:
                    self._next_allowed = self.monotonic() + self.delay + 3.0
                    if attempt == max_attempts - 1:
                        raise RemoteError("network_error", f"POST batch failed: {exc}", True) from exc
                    continue

                self._next_allowed = self.monotonic() + self.delay
                if resp.status_code == 200:
                    try:
                        payload = json.loads(resp.body)
                        if not isinstance(payload, list):
                            raise RemoteError("invalid_payload", "Expected list in batch response")
                        return payload
                    except Exception as exc:
                        raise RemoteError("invalid_payload", f"Malformed JSON: {exc}") from exc

                if resp.status_code == 429:
                    cooldown = min(120.0, max(self._retry_after(resp.headers), 15.0 * (1.5 ** attempt)))
                    self._next_allowed = self.monotonic() + cooldown
                    if attempt == max_attempts - 1:
                        raise RemoteError("rate_limited", f"HTTP 429: wait {cooldown:.0f}s", True, 429)
                    continue

                if resp.status_code in (400, 413):
                    raise RemoteError("remote_error", f"HTTP {resp.status_code}", False, resp.status_code)

                if resp.status_code in (500, 502, 503, 504):
                    if attempt == max_attempts - 1:
                        raise RemoteError("remote_error", f"HTTP {resp.status_code}", True, resp.status_code)
                    self._next_allowed = self.monotonic() + 1.0
                    continue

                raise RemoteError("remote_error", f"Unexpected HTTP {resp.status_code}", False, resp.status_code)

        raise RemoteError("network_error", "Retries exhausted")

    def fetch_batch_adaptive(self, arxiv_ids: list[str], max_chunk: int = 100) -> list[dict | None]:
        """Fetch batch with automatic splitting (100 -> 50 -> 25) if payload is too big or fails."""
        if not arxiv_ids:
            return []
        chunk_size = min(len(arxiv_ids), max_chunk)
        results: list[dict | None] = []

        i = 0
        while i < len(arxiv_ids):
            current_ids = arxiv_ids[i:i + chunk_size]
            try:
                chunk_res = self.fetch_batch(current_ids)
                results.extend(chunk_res)
                i += len(current_ids)
            except RemoteError as exc:
                if exc.info.get("http_status") in (400, 413) or "size" in exc.args[0].lower():
                    if chunk_size > 1:
                        chunk_size = max(1, chunk_size // 2)
                        continue
                raise
        return results
