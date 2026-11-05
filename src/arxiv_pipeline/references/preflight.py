"""One authenticated references probe, outside the measured crawl.

Only the API key in the environment is accepted by the CLI. Diagnostics contain
no request headers, response bodies, key values or upstream exception messages.
Existing scheduler deadlines are respected and the probe's slot is persisted
when resuming a corpus, so it shares pacing with subsequent measured requests.
"""
import argparse
from contextlib import ExitStack
import json
import math
import os
from pathlib import Path
import sqlite3
import time

from ..discovery.api import RemoteError
from ..http import (RequestScheduler, S2_WINDOW_REQUESTS, S2_WINDOW_SECONDS,
                    canonical_checkpoint_key, parse_utc_iso)
from ..runner.checkpoints import SQLiteCheckpointStore
from ..runner.state import OutputLock, StateStore
from .api import SemanticScholarClient


class PreflightBlocked(ValueError):
    """Actionable local reason whose public fields never contain upstream text."""
    def __init__(self, code, message, *, wait_seconds=None):
        super().__init__(message)
        self.code, self.wait_seconds = code, wait_seconds


class PreflightRateLimited(RemoteError):
    """Preserve a safe numeric deadline even before a corpus database exists."""

    def __init__(self, wait_seconds):
        super().__init__('rate_limited', 'Semantic Scholar returned HTTP 429', True, 429)
        self.wait_seconds = wait_seconds


def _saved_wait_seconds(checkpoints):
    record = checkpoints.load('rate_limits', canonical_checkpoint_key('semantic_scholar'))
    if record is None:
        return 0
    payload = record['payload']
    now = time.time()
    deadline = parse_utc_iso(payload['next_allowed_at'])
    stamps = sorted(parse_utc_iso(s) for s in payload['recent_requests_at'])
    recent = [stamp for stamp in stamps if stamp > now - S2_WINDOW_SECONDS]
    if len(recent) >= S2_WINDOW_REQUESTS:
        deadline = max(deadline, recent[-S2_WINDOW_REQUESTS] + S2_WINDOW_SECONDS)
    return max(0, math.ceil(deadline - now))


def remaining_cooldown_seconds(output_root, run_id):
    """Inspect durable S2 deadlines/window without key, HTTP or database writes."""
    path = Path(output_root) / 'state.sqlite'
    if not path.is_file():
        return 0
    with StateStore.readonly(path, run_id) as store:
        store.read_run_record()
        checkpoints = SQLiteCheckpointStore(store)
        RequestScheduler(checkpoints=checkpoints)  # Validate the entire saved payload first.
        return _saved_wait_seconds(checkpoints)


def check_access(arxiv_id, *, api_key, transport=None, output_root=None, run_id=None):
    if not isinstance(api_key, str) or not api_key.strip():
        raise PreflightBlocked('missing_key', 'S2_API_KEY is required')
    if api_key != api_key.strip() or '\n' in api_key or '\r' in api_key:
        raise PreflightBlocked('invalid_key_format', 'S2_API_KEY must be a single trimmed value')
    if (output_root is None) != (run_id is None):
        raise ValueError('output_root and run_id must be provided together')
    with ExitStack() as stack:
        scheduler = None
        if output_root is not None and (Path(output_root) / 'state.sqlite').is_file():
            stack.enter_context(OutputLock(Path(output_root)))
            store = stack.enter_context(StateStore(Path(output_root) / 'state.sqlite', run_id))
            store.read_run_record()  # Reject an unknown run before HTTP.
            checkpoints = SQLiteCheckpointStore(store)
            # Constructing the scheduler validates the complete saved pacing payload.
            scheduler = RequestScheduler(checkpoints=checkpoints)
            waiting = _saved_wait_seconds(checkpoints)
            if waiting:
                raise PreflightBlocked('cooldown', 'Saved Semantic Scholar cooldown active',
                                       wait_seconds=waiting)
        client = SemanticScholarClient(api_key=api_key, max_attempts=1, timeout=20,
                                       transport=transport, scheduler=scheduler)
        try:
            page = client.references_page(arxiv_id, 0, 1)
        except RemoteError as exc:
            if exc.info['http_status'] == 429:
                # The one-attempt client already parsed both Retry-After forms.
                # Without SQLite its in-memory deadline is the only source.
                waiting = max(0, math.ceil(client._next_allowed - client.monotonic()))
                if scheduler is not None:
                    waiting = max(waiting, _saved_wait_seconds(checkpoints))
                raise PreflightRateLimited(waiting) from None
            raise
        return {'ok': True, 'http_status': 200, 'returned_count': len(page['data'])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arxiv-id', required=True)
    parser.add_argument('--output-root', type=Path)
    parser.add_argument('--run-id')
    args = parser.parse_args(argv)
    try:
        result = check_access(args.arxiv_id, api_key=os.environ.get('S2_API_KEY'),
                              output_root=args.output_root, run_id=args.run_id)
    except RemoteError as exc:
        result = {'ok': False, 'code': exc.info['code'], 'http_status': exc.info['http_status']}
        if exc.info['http_status'] == 429:
            result['wait_seconds'] = exc.wait_seconds if isinstance(exc, PreflightRateLimited) else 0
        if exc.info['http_status'] == 429 and args.output_root is not None:
            try:
                result['wait_seconds'] = max(result['wait_seconds'],
                    remaining_cooldown_seconds(args.output_root, args.run_id))
            except (ValueError, OSError, sqlite3.Error):
                result = {'ok': False, 'code': 'invalid_preflight_configuration', 'http_status': None}
    except PreflightBlocked as exc:
        result = {'ok': False, 'code': exc.code, 'http_status': None}
        if exc.wait_seconds is not None:
            result['wait_seconds'] = exc.wait_seconds
    except (ValueError, OSError, sqlite3.Error):
        result = {'ok': False, 'code': 'invalid_preflight_configuration', 'http_status': None}
    print(json.dumps(result))
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
