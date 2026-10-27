"""Offline tests for T4 resume features: page checkpoints, date cache, persistent API waiting schedule."""

import json
import tempfile
import unittest
from pathlib import Path

from arxiv_pipeline.discovery.api import ApiResponse, RemoteError
from arxiv_pipeline.http import MAX_RESTORED_WAIT, RequestScheduler
from arxiv_pipeline.references import (JsonFileCheckpointStore, MemoryCheckpointStore, ReferencesModule)
from arxiv_pipeline.references.api import SemanticScholarClient
from arxiv_pipeline.references.checkpoint import DATES_NAMESPACE, PAGES_NAMESPACE, make_record
from arxiv_pipeline.http import SCHEDULER_NAMESPACE, canonical_checkpoint_key
from arxiv_pipeline.references.api import ARXIV_API, S2_FIELDS
from arxiv_pipeline.runner.checkpoints import InMemoryCheckpointStore as RunnerCheckpointStore
from arxiv_pipeline.references.cli import main as cli_main
from arxiv_pipeline.references.examples import FakeDates, FakeReader, cited, page

from test_references import D1, D2, TASK, check_contract, ctx

IDS = ["2001.00001", "2001.00002", "2001.00003"]
ALL_DATES = {IDS[0]: D1, IDS[1]: D1, IDS[2]: D2}


def three_pages():
    return [page([cited(IDS[0])], 1), page([cited(IDS[1])], 2), page([cited(IDS[2])])]


def module(replies, dates=None, store=None, **kw):
    reader, date_reader = FakeReader(replies), dates if dates is not None else FakeDates(ALL_DATES)
    return ReferencesModule(reader, date_reader, store=store, page_size=1, **kw), reader, date_reader


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class PageResumeTests(unittest.TestCase):
    def test_without_store_behaviour_is_unchanged(self):
        m, reader, _ = module(three_pages())
        result = m.fetch_references(TASK, ctx())
        check_contract(self, result)
        self.assertEqual(result["status"], "success")
        self.assertNotIn("pages_resumed", result["provenance"])

    def test_shared_pipeline_store_uses_contract_namespace_keys_and_payloads(self):
        store = RunnerCheckpointStore()
        m, _, _ = module(three_pages(), store=store)
        result = m.fetch_references(TASK, ctx())
        self.assertEqual(result["status"], "success")
        page_key = canonical_checkpoint_key(TASK["arxiv_id"])
        page_record = store.load("references", page_key)
        self.assertIsNotNone(page_record)
        self.assertEqual(set(page_record["payload"]), {"owner", "arxiv_id", "page_size", "fields", "pages"})
        self.assertEqual(set(page_record["payload"]["pages"][0]),
                         {"offset", "next_offset", "raw_items", "fetched_at"})
        date_key = canonical_checkpoint_key(IDS[0])
        date_record = store.load("arxiv_dates", date_key)
        self.assertEqual(set(date_record["payload"]), {"arxiv_id", "submission_date", "source", "fetched_at"})
        self.assertEqual(date_record["payload"]["source"], ARXIV_API)
        m2, r2, d2 = module([], store=store)
        self.assertEqual(m2.fetch_references(TASK, ctx())["status"], "success")
        self.assertEqual((r2.calls, d2.calls), ([], []))

    def test_resume_restarts_from_zero_when_page_size_or_fields_change(self):
        store = RunnerCheckpointStore()
        m1, _, _ = module([three_pages()[0], RemoteError("network_error", "down", True)], store=store)
        m1.fetch_references(TASK, ctx())
        reader = FakeReader(three_pages())
        m2 = ReferencesModule(reader, FakeDates(ALL_DATES), page_size=2, checkpoints=store)
        result = m2.fetch_references(TASK, ctx())
        self.assertEqual(result["status"], "success")
        self.assertEqual(reader.calls[0][1], 0)

    def test_failure_on_page_two_then_resume_requests_only_missing_pages(self):
        store = MemoryCheckpointStore()
        error = RemoteError("rate_limited", "429", True, 429)
        m1, r1, _ = module([three_pages()[0], error], store=store)
        first = m1.fetch_references(TASK, ctx())
        check_contract(self, first)
        self.assertEqual((first["status"], first["data"]["complete"]), ("partial", False))
        m2, r2, _ = module(three_pages()[1:], store=store)
        second = m2.fetch_references(TASK, ctx())
        check_contract(self, second)
        self.assertEqual([c[1] for c in r2.calls], [1, 2])  # offset 0 is not requested again
        self.assertEqual(second["status"], "success")
        self.assertEqual(list(second["data"]["references"]), IDS)
        self.assertEqual(len(second["data"]["raw_references"]), 3)
        self.assertEqual(second["metrics"]["s2_reference_count"], 3)
        self.assertEqual(second["provenance"]["pages_resumed"], "1")
        self.assertEqual(second["provenance"]["pages_fetched"], "3")

    def test_interrupt_midway_keeps_accepted_pages(self):
        store = MemoryCheckpointStore()
        m1, _, _ = module([three_pages()[0], three_pages()[1], KeyboardInterrupt()], store=store)
        with self.assertRaises(KeyboardInterrupt):
            m1.fetch_references(TASK, ctx())
        m2, r2, _ = module(three_pages()[2:], store=store)
        result = m2.fetch_references(TASK, ctx())
        check_contract(self, result)
        self.assertEqual([c[1] for c in r2.calls], [2])
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["data"]["raw_references"]), 3)

    def test_resume_after_failure_keeps_checkpointed_data_instead_of_null(self):
        store = MemoryCheckpointStore()
        m1, _, _ = module([three_pages()[0], RemoteError("network_error", "down", True)], store=store)
        m1.fetch_references(TASK, ctx())
        m2, _, _ = module([RemoteError("network_error", "still down", True)], store=store)
        result = m2.fetch_references(TASK, ctx())
        check_contract(self, result)
        self.assertEqual(result["status"], "partial")  # not failed/data=None: good pages are kept
        self.assertEqual(result["metrics"]["s2_reference_count"], 1)
        self.assertFalse(result["data"]["complete"])

    def test_complete_checkpoint_is_reused_without_semantic_scholar_requests(self):
        store = MemoryCheckpointStore()
        m1, _, _ = module(three_pages(), store=store)
        m1.fetch_references(TASK, ctx())
        m2, r2, d2 = module([], store=store)
        result = m2.fetch_references(TASK, ctx())
        check_contract(self, result)
        self.assertEqual((r2.calls, d2.calls, result["attempts"]), ([], [], 0))
        self.assertEqual(result["status"], "success")

    def test_original_raw_order_and_duplicates_survive_resume(self):
        store = MemoryCheckpointStore()
        p1 = page([cited(IDS[0]), cited(None)], 2)
        p2 = page([cited(IDS[0], title="Again")])
        m1, _, _ = module([p1, RemoteError("network_error", "x", True)], store=store, )
        m1.page_size = 2
        m1.fetch_references(TASK, ctx())
        m2, _, _ = module([p2], FakeDates({IDS[0]: D1}), store=store)
        m2.page_size = 2
        result = m2.fetch_references(TASK, ctx())
        check_contract(self, result)
        raw = result["data"]["raw_references"]
        self.assertEqual([i["citedPaper"]["title"] for i in raw], ["SYNTHETIC reference"] * 2 + ["Again"])
        self.assertEqual(result["metrics"], {"s2_reference_count": 3, "arxiv_reference_count": 1,
                                             "complete_reference_count": 1})

    def test_corrupt_or_foreign_checkpoints_are_ignored_not_fatal(self):
        key = canonical_checkpoint_key(TASK["arxiv_id"])
        page_record = {"offset": 0, "next_offset": None, "raw_items": [], "fetched_at": "2026-10-27T00:00:00Z"}
        good = {"owner": TASK["owner"], "arxiv_id": TASK["arxiv_id"], "page_size": 1,
                "fields": S2_FIELDS, "pages": [page_record]}
        bad_payloads = [
            "not a record", {"payload": good},
            make_record(PAGES_NAMESPACE, canonical_checkpoint_key("other-id"), good),
            make_record(PAGES_NAMESPACE, key, {**good, "pages": "x"}),
            make_record(PAGES_NAMESPACE, key, {**good, "pages": [{**page_record, "raw_items": [1]}]}),
            make_record(PAGES_NAMESPACE, key, {**good, "page_size": 2}),
            make_record(PAGES_NAMESPACE, key, {**good, "fields": "different-fields"}),
            {**make_record(PAGES_NAMESPACE, key, good), "format_version": 99},
        ]
        for bad in bad_payloads:
            with self.subTest(bad=str(bad)[:40]):
                store = MemoryCheckpointStore()
                store.records[(PAGES_NAMESPACE, key)] = bad
                m, r, _ = module(three_pages(), store=store)
                result = m.fetch_references(TASK, ctx())
                self.assertEqual(len(r.calls), 3)  # started from scratch
                self.assertEqual(result["status"], "success")

    def test_checkpoint_write_failure_is_reported_and_does_not_lose_the_result(self):
        store = MemoryCheckpointStore()
        store.fail_puts = True
        m, _, _ = module(three_pages(), store=store)
        result = m.fetch_references(TASK, ctx())
        check_contract(self, result)
        self.assertEqual(result["status"], "success")
        self.assertIn("checkpoint_write_errors", result["provenance"])

    def test_json_file_store_survives_a_new_process_and_old_ids_with_slash(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = {"owner": "man-nhi", "arxiv_id": "hep-th/9901001"}
            m1, _, _ = module([page([cited(IDS[0])], 1), RemoteError("network_error", "x", True)],
                              store=JsonFileCheckpointStore(tmp))
            m1.fetch_references(task, ctx())
            m2, r2, _ = module([page([cited(IDS[1])])], store=JsonFileCheckpointStore(tmp))
            result = m2.fetch_references(task, ctx())
            self.assertEqual([c[1] for c in r2.calls], [1])
            self.assertEqual(result["metrics"]["s2_reference_count"], 2)


class DateCacheTests(unittest.TestCase):
    def test_cached_dates_are_not_requested_again_across_papers(self):
        store = MemoryCheckpointStore()
        m1, _, d1 = module(three_pages(), store=store)
        m1.fetch_references(TASK, ctx())
        self.assertEqual(d1.calls, [IDS])
        other = {"owner": "man-nhi", "arxiv_id": "2501.12643"}
        m2, _, d2 = module([page([cited(IDS[0]), cited(IDS[1]), cited("2001.00004")])],
                           FakeDates({"2001.00004": D2}), store=store)
        result = m2.fetch_references(other, ctx())
        self.assertEqual(d2.calls, [["2001.00004"]])
        self.assertEqual(result["provenance"]["dates_from_cache"], "2")
        self.assertEqual(result["data"]["references"][IDS[0]]["submission_date"], D1)
        self.assertEqual(result["status"], "success")

    def test_failed_batch_keeps_cache_and_retry_asks_only_for_missing_dates(self):
        store = MemoryCheckpointStore()
        m1, _, _ = module(three_pages(), FakeDates(error=RemoteError("network_error", "down", True)),
                          store=store, date_batch_size=2)
        first = m1.fetch_references(TASK, ctx())
        self.assertEqual(first["status"], "partial")
        self.assertEqual(first["provenance"]["date_lookup_batches_failed"], "2")
        # Second run: pages come from the checkpoint; the arXiv API now answers.
        m2, r2, d2 = module([], FakeDates(ALL_DATES), store=store, date_batch_size=2)
        second = m2.fetch_references(TASK, ctx())
        check_contract(self, second)
        self.assertEqual(r2.calls, [])
        self.assertEqual(second["status"], "success")
        # Partial success: only the first batch worked, the retry asks for what is still missing.
        store2 = MemoryCheckpointStore()

        class OneGoodBatch(FakeDates):
            def submission_dates(self, ids):
                if self.calls:
                    self.calls.append(list(ids))
                    raise RemoteError("network_error", "down", True)
                return super().submission_dates(ids)
        m3, _, _ = module(three_pages(), OneGoodBatch(ALL_DATES), store=store2, date_batch_size=2)
        self.assertEqual(m3.fetch_references(TASK, ctx())["status"], "partial")
        m4, _, d4 = module([], FakeDates(ALL_DATES), store=store2, date_batch_size=2)
        self.assertEqual(m4.fetch_references(TASK, ctx())["status"], "success")
        self.assertEqual(d4.calls, [[IDS[2]]])

    def test_missing_dates_are_not_cached_and_bad_cache_values_are_ignored(self):
        store = MemoryCheckpointStore()
        m1, _, _ = module(three_pages(), FakeDates({IDS[0]: D1}), store=store)
        self.assertEqual(m1.fetch_references(TASK, ctx())["status"], "partial")
        self.assertIsNone(store.get(DATES_NAMESPACE, canonical_checkpoint_key(IDS[1])))  # not cached
        date_key = canonical_checkpoint_key(IDS[0])
        store.records[(DATES_NAMESPACE, date_key)] = make_record(
            DATES_NAMESPACE, date_key, {"arxiv_id": IDS[0], "submission_date": "2020",
                                         "source": ARXIV_API, "fetched_at": "2026-10-27T00:00:00Z"})
        m2, _, d2 = module([], FakeDates(ALL_DATES), store=store)
        result = m2.fetch_references(TASK, ctx())
        self.assertEqual(result["status"], "success")
        self.assertIn(IDS[0], d2.calls[0])  # invalid cached value was re-fetched

    def test_dates_not_cached_when_adapter_returns_invalid_or_foreign_ids(self):
        store = MemoryCheckpointStore()
        m, _, _ = module([page([cited(IDS[0])])], FakeDates({IDS[0]: "N/A", "2999.00001": D1}), store=store)
        m.fetch_references(TASK, ctx())
        self.assertEqual([k for k in store.records if k[0] == DATES_NAMESPACE], [])


class SchedulerPersistenceTests(unittest.TestCase):
    def new(self, store, clock, wall_offset=1_000_000.0):
        return RequestScheduler(monotonic=clock.monotonic, sleep=clock.sleep, store=store,
                                wall_clock=lambda: wall_offset + clock.now)

    def test_arxiv_gap_survives_restart(self):
        store, c = MemoryCheckpointStore(), Clock()
        with self.new(store, c).request("arxiv"):
            pass
        c.now = 1.0  # a "restart" one second later; the new process has a fresh monotonic origin
        c2 = Clock()
        c2.now = 5000.0
        s2 = RequestScheduler(monotonic=c2.monotonic, sleep=c2.sleep, store=store,
                              wall_clock=lambda: 1_000_000.0 + c.now + (c2.now - 5000.0))
        starts = []
        with s2.request("arxiv"):
            starts.append(c2.now)
        self.assertEqual(starts, [5002.0])  # waited the remaining 2 of the 3 seconds

    def test_s2_cooldown_and_429_streak_survive_restart(self):
        store, c = MemoryCheckpointStore(), Clock()
        s = self.new(store, c)
        s.rate_limited("semantic_scholar", 0.0)   # streak 1 -> 30 s
        s.rate_limited("semantic_scholar", 200.0)  # streak 2 -> server asks 200 s (> 60)
        c.now = 50.0
        s2 = self.new(store, c)
        self.assertTrue(s2.restored)
        with s2.request("semantic_scholar"):
            pass
        self.assertEqual(c.now, 200.0)  # waited the rest of Retry-After across the restart
        self.assertEqual(s2.rate_limited("semantic_scholar", 0.0), 120.0)  # streak 3 continues, not reset
        s2.request_succeeded("semantic_scholar")
        self.assertEqual(self.new(store, c).rate_limited("semantic_scholar", 0.0), 30.0)  # reset persisted

    def test_s2_rolling_window_survives_restart(self):
        store, c = MemoryCheckpointStore(), Clock()
        s = self.new(store, c)
        for _ in range(100):
            with s.request("semantic_scholar"):
                pass
        c.now += 10
        s2 = self.new(store, c)
        with s2.request("semantic_scholar"):
            pass
        self.assertGreaterEqual(c.now, 300.0)  # the 101st request waits for the window of the first

    def test_expired_and_corrupt_state_is_ignored(self):
        store, c = MemoryCheckpointStore(), Clock()
        s = self.new(store, c)
        s.defer("arxiv", 60)
        c.now = 500.0
        fresh = self.new(store, c)
        with fresh.request("arxiv"):
            pass
        self.assertEqual(c.now, 500.0)  # expired wait is not applied
        for payload in ({"next_allowed_at": "soon"}, {"next_allowed_at": float("nan")}, {"s2_429_streak": -3},
                        {"requests": "x"}, "junk"):
            bad = MemoryCheckpointStore()
            key_arxiv = canonical_checkpoint_key("arxiv")
            key_s2 = canonical_checkpoint_key("semantic_scholar")
            bad.records[(SCHEDULER_NAMESPACE, key_arxiv)] = make_record(
                SCHEDULER_NAMESPACE, key_arxiv,
                {"group": "arxiv", "next_allowed_at": "soon", "recent_requests_at": [], "consecutive_429": -3}) \
                if isinstance(payload, dict) else payload
            bad.records[(SCHEDULER_NAMESPACE, key_s2)] = bad.records[(SCHEDULER_NAMESPACE, key_arxiv)]
            c0 = Clock()
            sched = self.new(bad, c0)
            with sched.request("arxiv"):
                pass
            self.assertEqual(c0.now, 0.0)

    def test_absurd_future_wait_is_capped(self):
        store, c = MemoryCheckpointStore(), Clock()
        key = canonical_checkpoint_key("arxiv")
        from arxiv_pipeline.http import utc_iso
        record = make_record(SCHEDULER_NAMESPACE, key, {
            "group": "arxiv", "next_allowed_at": utc_iso(1_000_000.0 + 10 ** 9),
            "recent_requests_at": [], "consecutive_429": 0})
        store.records[(SCHEDULER_NAMESPACE, key)] = record
        s = self.new(store, c)
        with s.request("arxiv"):
            pass
        self.assertEqual(c.now, MAX_RESTORED_WAIT)

    def test_store_write_errors_do_not_break_requests_and_are_counted(self):
        store, c = MemoryCheckpointStore(), Clock()
        store.fail_puts = True
        s = self.new(store, c)
        with s.request("arxiv"):
            pass
        s.defer("arxiv", 5)
        self.assertGreaterEqual(s.persist_failures, 2)

    def test_scheduler_without_store_is_unchanged(self):
        c = Clock()
        s = RequestScheduler(monotonic=c.monotonic, sleep=c.sleep)
        with s.request("arxiv"):
            pass
        self.assertFalse(s.restored)
        self.assertEqual(s.persist_failures, 0)

    def test_shared_pipeline_store_uses_rate_limit_contract_and_iso_timestamps(self):
        store, c = RunnerCheckpointStore(), Clock()
        scheduler = RequestScheduler(checkpoints=store, monotonic=c.monotonic, sleep=c.sleep,
                                     wall_clock=lambda: 1_000_000.0 + c.now)
        scheduler.rate_limited("semantic_scholar", 45)
        key = canonical_checkpoint_key("semantic_scholar")
        record = store.load("rate_limits", key)
        self.assertEqual(set(record["payload"]),
                         {"group", "next_allowed_at", "recent_requests_at", "consecutive_429"})
        self.assertEqual(record["payload"]["group"], "semantic_scholar")
        self.assertTrue(record["payload"]["next_allowed_at"].endswith("Z"))
        self.assertEqual(record["payload"]["consecutive_429"], 1)

    def test_client_cooldown_after_429_is_honoured_by_a_restarted_client(self):
        store, c = MemoryCheckpointStore(), Clock()
        sched = self.new(store, c)
        starts = []

        def busy(*args):
            starts.append(c.now)
            return ApiResponse(429, {"Retry-After": "90"}, b"")
        client = SemanticScholarClient(transport=busy, scheduler=sched, max_attempts=1, api_key="",
                                       monotonic=c.monotonic, sleep=c.sleep)
        with self.assertRaises(RemoteError):
            client.references_page("2501.12642", 0, 10)
        c.now += 5  # restart
        body = json.dumps({"data": []}).encode()

        def ok(*args):
            starts.append(c.now)
            return ApiResponse(200, {}, body)
        client2 = SemanticScholarClient(transport=ok, scheduler=self.new(store, c), api_key="",
                                        monotonic=c.monotonic, sleep=c.sleep)
        client2.references_page("2501.12642", 0, 10)
        self.assertEqual(starts, [0.0, 90.0])


class CliResumeTests(unittest.TestCase):
    def test_cli_accepts_state_dir_and_rejects_bad_input_before_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = cli_main(["--owner", "man-nhi", "--arxiv-id", "bad-id", "--state-dir", str(Path(tmp) / "s"),
                             "--output-root", tmp])
            self.assertEqual(code, 1)
            self.assertFalse((Path(tmp) / "s").exists())


if __name__ == "__main__":
    unittest.main()
