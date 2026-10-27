"""Offline behavioral tests: no requests are sent to arXiv."""

import json

from pathlib import Path

import tempfile

import unittest

from unittest.mock import patch as mock_patch
from urllib.error import URLError

from arxiv_pipeline.discovery import DiscoveryModule, candidate_ids, candidate_count
from arxiv_pipeline.discovery.cli import main

from arxiv_pipeline.discovery.api import ApiClient, ApiResponse, RemoteError, parse_feed

def config(start=17523, end=17525, month="2025-04", end_month=None, inclusive=True):
    return dict(owner="trong", start_month=month, start_id=start,
                end_month=end_month or month, end_id=end, inclusive=inclusive)

def context(tmp_path):
    return dict(run_id="offline-001", is_mock=True, output_root=tmp_path)

def feed(ids=(), total=None, start=0, extra=""):
    entries = "".join(f"<entry><id>https://arxiv.org/abs/{i}</id></entry>" for i in ids)
    return (f'<feed xmlns="http://www.w3.org/2005/Atom" '
            f'xmlns:o="http://a9.com/-/spec/opensearch/1.1/">'
            f'<o:totalResults>{len(ids) if total is None else total}</o:totalResults>'
            f'<o:startIndex>{start}</o:startIndex>{entries}{extra}</feed>').encode()

class FakeClient:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []
        self.attempts = 0

    def query(self, ids):
        self.calls.append(list(ids))
        self.attempts += 1
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return reply

class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="arxiv-t1-test-")
        self.addCleanup(folder.cleanup)
        self.tmp_path = Path(folder.name)

    def test_all_assigned_counts_and_boundaries(self):
        from arxiv_pipeline.config import ASSIGNED_RANGES
        expected = {'trong': 83142, 'khang': 80870, 'hoang': 5000,
                    'man-nhi': 5000, 'huynh-nhi': 5000}
        for c in ASSIGNED_RANGES:
            with self.subTest(owner=c['owner']):
                assert candidate_count(c) == expected[c['owner']]
                ids = list(candidate_ids(c))
                assert len(ids) == len(set(ids)) == candidate_count(c)
                assert ids[0] == c['start_month'][2:].replace('-', '') + f".{c['start_id']:05d}"
                assert ids[-1] == c['end_month'][2:].replace('-', '') + f".{c['end_id']:05d}"

    def test_multi_month_range(self):
        c = config(99999, 1, end_month='2025-06')
        assert candidate_count(c) == 100001
        ids = list(candidate_ids(c))
        assert ids[:2] == ['2504.99999', '2505.00001']
        assert ids[-2:] == ['2505.99999', '2506.00001']

    def test_network_retry_and_http_date(self):
        clock = Clock()
        responses = iter([URLError('offline'), ApiResponse(200, {}, feed())])
        def transport(*args):
            value = next(responses)
            if isinstance(value, Exception):
                raise value
            return value
        client = ApiClient(transport=transport, monotonic=clock.monotonic,
                           sleep=clock.sleep, wall_clock=lambda: 0)
        client.query(['2504.17523'])
        assert client.attempts == 2 and clock.sleeps == [3]
        assert client._retry_after({'retry-after': 'Thu, 01 Jan 1970 00:00:09 GMT'}) == 9
        assert client._retry_after({'Retry-After': 'NaN'}) == 0
        assert client._retry_after({'Retry-After': 'invalid'}) == 0

    def test_reusing_client_counts_requests_for_each_call(self):
        client = FakeClient([feed(['2504.17523']), feed(['2504.17523'])])
        module = DiscoveryModule(client)
        for _ in range(2):
            result = module.discover(config(17523, 17523), context(self.tmp_path))
            assert result['attempts'] == 1

    def test_zero_found_but_verified_exclusions_is_useful(self):
        client = FakeClient([feed(), RemoteError('network_error', 'offline', True)])
        result = DiscoveryModule(client, batch_size=1).discover(config(17523, 17524), context(self.tmp_path))
        assert result['status'] == 'partial' and result['data']['papers'] == []
        assert result['data']['unresolved_ids'] == ['2504.17524']
        assert len(result['data']['excluded_ids']) == 1

    def test_response_without_pagination_can_confirm_presence(self):
        body = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>https://arxiv.org/abs/2504.17523v1</id></entry></feed>'
        result = DiscoveryModule(FakeClient([body])).discover(config(17523, 17523), context(self.tmp_path))
        assert result['status'] == 'success' and result['data']['complete']

    def test_cli_full_range_and_failed_exit(self):
        with mock_patch('arxiv_pipeline.discovery.cli.ASSIGNED_RANGES', [config(17523, 17523)]):
            with mock_patch('arxiv_pipeline.discovery.cli.ApiClient', return_value=FakeClient([feed(['2504.17523'])])):
                code = main(['--owner', 'trong', '--run-id', 'full', '--output-root', str(self.tmp_path), '--full-range'])
                assert code == 0
            with mock_patch('arxiv_pipeline.discovery.cli.ApiClient', return_value=FakeClient([RemoteError('network_error', 'offline', True)])):
                code = main(['--owner', 'trong', '--run-id', 'fail', '--output-root', str(self.tmp_path), '--full-range'])
                assert code == 1
        assert json.loads((self.tmp_path / 'preview/fail/trong.json').read_text())['data'] is None

    def test_context_and_constructor_rejected_before_network(self):
        for changes in [dict(run_id='../bad'), dict(is_mock='false'), dict(output_root='')]:
            ctx = context(self.tmp_path)
            ctx.update(changes)
            client = FakeClient([])
            with self.assertRaises(ValueError):
                DiscoveryModule(client).discover(config(), ctx)
            assert client.calls == []
        for kwargs in [dict(batch_size=0), dict(batch_size=101), dict(max_candidates=0), dict(max_candidates=True)]:
            with self.assertRaises(ValueError):
                DiscoveryModule(FakeClient([]), **kwargs)
        for kwargs in [dict(delay=0), dict(timeout=float('nan')), dict(max_attempts=4)]:
            with self.assertRaises(ValueError):
                ApiClient(**kwargs)

    def test_result_envelope_matches_contract_keys(self):
        from arxiv_pipeline.contracts import Result
        client = FakeClient([feed(['2504.17523'])])
        result = DiscoveryModule(client).discover(config(17523, 17523), context(self.tmp_path))
        assert set(result) == set(Result.__required_keys__)
        assert result['schema_version'] == '1.2'
        assert result['owner'] == 'trong' and result['run_id'] == 'offline-001'
        assert result['error'] is None and result['missing_fields'] == []
        assert result['duration_seconds'] >= 0
        json.dumps(result, allow_nan=False)

    def test_cross_month_and_exclusive(self):
        c = config(99998, 2, end_month='2025-05')
        assert list(candidate_ids(c)) == ['2504.99998', '2504.99999', '2505.00001', '2505.00002']
        assert candidate_count(c) == 4
        c['inclusive'] = False
        assert list(candidate_ids(c)) == ['2504.99999', '2505.00001']
        assert candidate_count(c) == 2
        assert list(candidate_ids(config(1, 1, inclusive=False))) == []

    def test_invalid_input_before_request(self):
        for changes in [dict(start_id=True), dict(end_id=100000), dict(start_month='2025-13'), dict(start_month='2014-12'), dict(owner='unknown'), dict(inclusive=1), dict(start_id=17526)]:
            c = config()
            c.update(changes)
            client = FakeClient([])
            with self.assertRaises(ValueError):
                DiscoveryModule(client).discover(c, context(self.tmp_path))
            assert client.calls == []

    def test_found_missing_reordered_and_versions(self):
        client = FakeClient([feed(['2504.17525v2', '2504.17523v1'])])
        r = DiscoveryModule(client).discover(config(), context(self.tmp_path))
        assert r['schema_version'] == '1.2'
        assert r['status'] == 'success' and r['data']['complete']
        assert r['arxiv_id'] is None and r['version'] is None
        assert [p['arxiv_id'] for p in r['data']['papers']] == ['2504.17523', '2504.17525']
        assert r['data']['excluded_ids'] == [dict(arxiv_id='2504.17524', reason='not_returned_by_complete_api_feed')]
        assert r['attempts'] == 1

    def test_limit_is_partial_and_does_not_change_config(self):
        c = config()
        client = FakeClient([feed(['2504.17523'])])
        r = DiscoveryModule(client, max_candidates=1).discover(c, context(self.tmp_path))
        assert r['status'] == 'partial' and (not r['data']['complete'])
        assert r['error'] is None and r['data']['unresolved_ids'] == []
        assert c['end_id'] == 17525
        assert r['provenance']['scope_limited'] == 'true'

    def test_gap_does_not_stop_scan(self):
        client = FakeClient([feed(), feed(['2504.17524']), feed(['2504.17525'])])
        r = DiscoveryModule(client, batch_size=1).discover(config(), context(self.tmp_path))
        assert len(client.calls) == 3
        assert r['status'] == 'success' and len(r['data']['papers']) == 2

    def test_truncated_batch_requeries_only_ambiguous_ids(self):
        client = FakeClient([feed(['2504.17523'], total=3), feed(['2504.17524']), feed()])
        r = DiscoveryModule(client).discover(config(), context(self.tmp_path))
        assert r['status'] == 'success'
        assert client.calls[1:] == [['2504.17524'], ['2504.17525']]
        assert len(r['data']['excluded_ids']) == 1

    def test_duplicate_entries_are_deduplicated(self):
        client = FakeClient([feed(['2504.17523v1', '2504.17523v2'])])
        r = DiscoveryModule(client).discover(config(17523, 17523), context(self.tmp_path))
        assert r['status'] == 'success' and len(r['data']['papers']) == 1

    def test_failure_is_not_exclusion_and_keeps_known_data(self):
        client = FakeClient([feed(['2504.17523']), RemoteError('network_error', 'offline', True)])
        r = DiscoveryModule(client, batch_size=1).discover(config(17523, 17524), context(self.tmp_path))
        assert r['status'] == 'partial'
        assert r['data']['unresolved_ids'] == ['2504.17524']
        assert r['data']['excluded_ids'] == []
        assert r['error']['code'] == 'network_error'

    def test_all_requests_fail(self):
        client = FakeClient([RemoteError('rate_limited', '429', True, 429)])
        r = DiscoveryModule(client).discover(config(), context(self.tmp_path))
        assert r['status'] == 'failed' and r['data'] is None
        assert r['error']['http_status'] == 429

    def test_empty_range_no_network(self):
        client = FakeClient([])
        r = DiscoveryModule(client).discover(config(1, 1, inclusive=False), context(self.tmp_path))
        assert r['status'] == 'success' and r['data']['papers'] == []
        assert r['attempts'] == 0 and client.calls == []

    def test_bad_payload(self):
        for body in [b'<html>error</html>', b'<feed', b'<!DOCTYPE x><x/>']:
            with self.assertRaises(RemoteError):
                parse_feed(body, ['2504.17523'])

    def test_error_entry_is_not_paper_or_exclusion(self):
        error = '<entry><id>https://arxiv.org/api/errors#bad</id><title>Error</title></entry>'
        client = FakeClient([feed(extra=error)])
        r = DiscoveryModule(client).discover(config(17523, 17523), context(self.tmp_path))
        assert r['status'] == 'failed' and r['error']['code'] == 'invalid_payload'

    def test_unexpected_id_does_not_confirm_missing(self):
        client = FakeClient([feed(['2504.17599']), feed(['2504.17523']), feed()])
        r = DiscoveryModule(client).discover(config(17523, 17524), context(self.tmp_path))
        assert r['status'] == 'success'
        assert len(r['data']['papers']) == 1 and len(client.calls) == 3

    def test_missing_pagination_remains_unresolved(self):
        body = b'<feed xmlns="http://www.w3.org/2005/Atom"/>'
        r = DiscoveryModule(FakeClient([body])).discover(config(17523, 17523), context(self.tmp_path))
        assert r['status'] == 'failed' and r['data'] is None

    def test_http_retry_after_and_pacing(self):
        clock = Clock()
        replies = iter([ApiResponse(429, {'Retry-After': '8'}, b''), ApiResponse(200, {}, feed()), ApiResponse(200, {}, feed())])
        calls = []

        def transport(url, timeout, headers):
            calls.append((clock.now, url))
            return next(replies)
        client = ApiClient(transport=transport, monotonic=clock.monotonic, sleep=clock.sleep)
        client.query(['2504.17523'])
        client.query(['2504.17524'])
        assert [c[0] for c in calls] == [0, 8, 11]
        assert 'max_results=1' in calls[0][1]
        assert client.attempts == 3

    def test_http_retry_limit_and_nonretryable(self):
        clock = Clock()
        client = ApiClient(transport=lambda *a: ApiResponse(503, {}, b''), monotonic=clock.monotonic, sleep=clock.sleep)
        with self.assertRaises(RemoteError) as error:
            client.query(['2504.17523'])
        assert error.exception.info['retryable'] and client.attempts == 3
        assert clock.sleeps == [3, 6]
        client = ApiClient(transport=lambda *a: ApiResponse(400, {}, b''))
        with self.assertRaises(RemoteError):
            client.query(['2504.17523'])
        assert client.attempts == 1

    def test_cli_default_preview_and_exit(self):
        fake = FakeClient([feed(['2504.17523'])])
        with mock_patch('arxiv_pipeline.discovery.cli.ApiClient', return_value=fake):
            code = main(['--owner', 'trong', '--run-id', 'demo', '--output-root', str(self.tmp_path)])
        saved = json.loads((self.tmp_path / 'preview/demo/trong.json').read_text())
        assert code == 2 and saved['status'] == 'partial'
        assert len(fake.calls[0]) == 10
        assert not (self.tmp_path / 'raw').exists()

    def test_cli_rejects_unsafe_run_and_conflicting_flags(self):
        assert main(['--owner', 'trong', '--run-id', '../bad', '--output-root', str(self.tmp_path)]) == 1
        with self.assertRaises(SystemExit) as exc:
            main(['--owner', 'trong', '--full-range', '--max-candidates', '10'])
        assert exc.exception.code == 2


if __name__ == "__main__":
    unittest.main()
