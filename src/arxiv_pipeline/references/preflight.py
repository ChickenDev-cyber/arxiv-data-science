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
            record = checkpoints.load('rate_limits', canonical_checkpoint_key('semantic_scholar'))
            if record is not None:
                payload = record['payload']
                now = time.time()
                deadline = parse_utc_iso(payload['next_allowed_at'])
                stamps = sorted(parse_utc_iso(s) for s in payload['recent_requests_at'])
                recent = [stamp for stamp in stamps if stamp > now - S2_WINDOW_SECONDS]
                if len(recent) >= S2_WINDOW_REQUESTS:
                    deadline = max(deadline, recent[-S2_WINDOW_REQUESTS] + S2_WINDOW_SECONDS)
                if deadline > now:
                    raise PreflightBlocked('cooldown', 'Saved Semantic Scholar cooldown active',
                                           wait_seconds=math.ceil(deadline-now))
        client = SemanticScholarClient(api_key=api_key, max_attempts=1, timeout=20,
                                       transport=transport, scheduler=scheduler)
        page = client.references_page(arxiv_id, 0, 1)
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
