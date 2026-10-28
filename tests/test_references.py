"""Offline behavioral tests for T4 (references). No request is sent to any server."""

import json
from copy import deepcopy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from arxiv_pipeline.discovery.api import ApiResponse, RemoteError
from arxiv_pipeline.references import ReferencesModule
from arxiv_pipeline.references.api import (ArxivDateClient, SemanticScholarClient, normalize_arxiv_id,
                                           parse_submission_dates, valid_iso_date)
from arxiv_pipeline.references.cli import main as cli_main
from arxiv_pipeline.references.examples import FakeDates, FakeReader, cited, page, scenarios
from arxiv_pipeline.references.examples import build as build_example

TASK = {"owner": "man-nhi", "arxiv_id": "2501.12642"}
D1, D2 = "2020-01-01T00:00:00Z", "2020-01-02T00:00:00Z"


def ctx():
    return {"run_id": "offline-001", "is_mock": True, "output_root": Path("/tmp/unused")}


def run(replies, dates=None, **kwargs):
    reader, date_reader = FakeReader(replies), dates if dates is not None else FakeDates()
    module = ReferencesModule(reader, date_reader, **kwargs)
    return module.fetch_references(TASK, ctx()), reader, date_reader


def resolve(document, pointer):
    current = document
    for token in pointer.split("/")[1:]:
        token = token.replace("~1", "/").replace("~0", "~")
        current = current[int(token)] if isinstance(current, list) else current[token]
    return current


def check_contract(test, result):
    """Section 5 + 9 invariants that a JSON Schema alone cannot express."""
    test.assertEqual(result["schema_version"], "1.2")
    test.assertEqual((result["step"], result["version"], result["arxiv_id"]), ("references", None, TASK["arxiv_id"]))
    test.assertEqual(result["owner"], TASK["owner"])
    status, data = result["status"], result["data"]
    test.assertIn(status, {"success", "partial", "empty", "not_found", "failed"})
    if status in ("failed", "not_found"):
        test.assertIsNone(data)
        test.assertIsNotNone(result["error"])
        return
    refs = data["references"]
    test.assertEqual(set(data), {"references", "raw_references", "complete"})
    test.assertIsInstance(data["raw_references"], list)
    test.assertTrue(all(isinstance(item, dict) for item in data["raw_references"]))
    test.assertIsInstance(data["complete"], bool)
    metrics = result["metrics"]
    test.assertEqual(set(metrics), {"s2_reference_count", "arxiv_reference_count", "complete_reference_count"})
    test.assertTrue(all(type(v) is int and v >= 0 for v in metrics.values()))
    test.assertLessEqual(metrics["complete_reference_count"], metrics["arxiv_reference_count"])
    test.assertLessEqual(metrics["arxiv_reference_count"], metrics["s2_reference_count"])
    test.assertEqual(metrics["arxiv_reference_count"], len(refs))
    test.assertEqual(metrics["s2_reference_count"], len(data["raw_references"]))
    for key, ref in refs.items():
        test.assertEqual(key, ref["arxiv_id"])
        test.assertEqual(set(ref), {"arxiv_id", "title", "authors", "submission_date", "semantic_scholar_id"})
    incomplete = {p for k, r in refs.items() for f in ("title", "authors", "submission_date", "semantic_scholar_id")
                  if r[f] is None for p in ["/data/references/%s/%s" % (k.replace("~", "~0").replace("/", "~1"), f)]}
    test.assertEqual(set(result["missing_fields"]), incomplete)
    for pointer in result["missing_fields"]:
        test.assertIsNone(resolve(result, pointer))
    test.assertEqual(metrics["complete_reference_count"],
                     sum(all(v is not None for v in r.values()) for r in refs.values()))
    if status in ("success", "empty"):
        test.assertIsNone(result["error"])
        test.assertTrue(data["complete"])
        test.assertEqual(result["missing_fields"], [])
    if status == "empty":
        test.assertEqual(refs, {})
    if status == "success":
        test.assertTrue(refs)
    test.assertTrue(all(isinstance(k, str) and isinstance(v, str) for k, v in result["provenance"].items()))


class SuccessAndStatusTests(unittest.TestCase):
    def test_success_has_flag_and_three_metrics(self):
        result = build_example("success", TASK["owner"])
        check_contract(self, result)
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["data"]["complete"])
        # 3 items received (one without arXiv ID), 2 kept, both with all four fields.
        self.assertEqual(result["metrics"], {"s2_reference_count": 3, "arxiv_reference_count": 2,
                                             "complete_reference_count": 2})

    def test_missing_arxiv_date_is_partial_but_complete_true(self):
        result = build_example("missing_date", TASK["owner"])
        check_contract(self, result)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["data"]["complete"])  # complete = reached the last page only
        self.assertEqual(result["missing_fields"], ["/data/references/2001.00002/submission_date"])
        self.assertIsNone(result["data"]["references"]["2001.00002"]["submission_date"])
        self.assertEqual(result["metrics"]["complete_reference_count"], 1)

    def test_no_arxiv_reference_is_empty_not_failed(self):
        result = build_example("empty", TASK["owner"])
        check_contract(self, result)
        self.assertEqual(result["status"], "empty")
        self.assertEqual(result["metrics"], {"s2_reference_count": 1, "arxiv_reference_count": 0,
                                             "complete_reference_count": 0})

    def test_zero_references_at_all_is_empty(self):
        result, _, dates = run([page([])])
        check_contract(self, result)
        self.assertEqual((result["status"], result["metrics"]["s2_reference_count"]), ("empty", 0))
        self.assertEqual(dates.calls, [])  # nothing to look up

    def test_later_page_error_is_partial_and_incomplete(self):
        result = build_example("page_error", TASK["owner"])
        check_contract(self, result)
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["data"]["complete"])
        self.assertEqual(result["error"]["code"], "rate_limited")
        self.assertEqual(result["metrics"]["arxiv_reference_count"], 1)  # only what was received

    def test_first_page_error_is_failed_with_null_data(self):
        error = RemoteError("network_error", "down", True)
        result, _, dates = run([error])
        check_contract(self, result)
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["data"])
        self.assertTrue(result["error"]["retryable"])
        self.assertEqual(result["metrics"], {})
        self.assertEqual(dates.calls, [])

    def test_unknown_paper_is_not_found_and_not_retryable(self):
        result, _, _ = run([RemoteError("not_found", "404", False, 404)])
        check_contract(self, result)
        self.assertEqual(result["status"], "not_found")
        self.assertEqual((result["error"]["code"], result["error"]["http_status"]), ("not_found", 404))
        self.assertFalse(result["error"]["retryable"])

    def test_empty_first_page_then_error_is_partial_incomplete(self):
        result, _, _ = run([page([], next_offset=1), RemoteError("remote_error", "500", True, 500)], page_size=1)
        check_contract(self, result)
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["data"]["complete"])
        self.assertEqual(result["data"]["references"], {})

    def test_title_or_authors_blank_do_not_count_as_complete(self):
        items = [cited("2001.00001", title="   "), cited("2001.00002", authors=("", " ")),
                 cited("2001.00003", paper_id="")]
        dates = FakeDates({"2001.00001": D1, "2001.00002": D1, "2001.00003": D1})
        result, _, _ = run([page(items)], dates)
        check_contract(self, result)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["metrics"]["complete_reference_count"], 0)
        self.assertEqual(set(result["missing_fields"]),
                         {"/data/references/2001.00001/title", "/data/references/2001.00002/authors",
                          "/data/references/2001.00003/semantic_scholar_id"})

    def test_date_lookup_failure_keeps_references_and_reports_error(self):
        dates = FakeDates(error=RemoteError("network_error", "down", True))
        result, _, _ = run([page([cited("2001.00001")])], dates)
        check_contract(self, result)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["data"]["complete"])
        self.assertEqual(result["error"]["code"], "network_error")
        self.assertEqual(result["provenance"]["date_lookup_batches_failed"], "1")


class MergeAndPagingTests(unittest.TestCase):
    def test_pages_follow_next_offset_until_exhausted(self):
        replies = [page([cited("2001.00001")], 1), page([cited("2001.00002")], 2), page([cited("2001.00003")])]
        result, reader, _ = run(replies, FakeDates({"2001.00001": D1, "2001.00002": D1, "2001.00003": D2}),
                                page_size=1)
        check_contract(self, result)
        self.assertEqual([c[1] for c in reader.calls], [0, 1, 2])
        self.assertEqual(result["status"], "success")
        self.assertEqual(list(result["data"]["references"]), ["2001.00001", "2001.00002", "2001.00003"])

    def test_duplicates_keep_first_non_missing_value_and_never_mix_authors(self):
        items = [cited("2001.00001", title="", authors=("A",), paper_id="s2-a"),
                 cited("2001.00001", title="Second title", authors=("B", "C"), paper_id="s2-b"),
                 cited("2001.00001", title="Third title", authors=("D",), paper_id="s2-c")]
        result, _, _ = run([page(items)], FakeDates({"2001.00001": D1}))
        check_contract(self, result)
        ref = result["data"]["references"]["2001.00001"]
        self.assertEqual((ref["title"], ref["authors"], ref["semantic_scholar_id"]), ("Second title", ["A"], "s2-a"))
        self.assertEqual(result["metrics"], {"s2_reference_count": 3, "arxiv_reference_count": 1,
                                             "complete_reference_count": 1})
        prov = result["provenance"]
        self.assertIn("selected s2_item=2", prov["/data/references/2001.00001/title"])
        self.assertIn("conflicting values in s2_item=3", prov["/data/references/2001.00001/title"])
        self.assertIn("selected s2_item=1", prov["/data/references/2001.00001/authors"])
        self.assertIn("conflicting values in s2_item=2,3", prov["/data/references/2001.00001/authors"])

    def test_duplicate_equal_values_report_no_conflict(self):
        items = [cited("2001.00001"), cited("2001.00001")]
        result, _, _ = run([page(items)], FakeDates({"2001.00001": D1}))
        self.assertNotIn("conflicting", result["provenance"]["/data/references/2001.00001/title"])

    def test_old_style_ids_are_kept_and_pointer_is_escaped(self):
        items = [cited("hep-th/9901001"), cited("math.GT/0309136v2"), cited("arXiv:1706.03762v5")]
        result, _, dates = run([page(items)], FakeDates({"hep-th/9901001": D1}))
        check_contract(self, result)
        self.assertEqual(list(result["data"]["references"]), ["hep-th/9901001", "math/0309136", "1706.03762"])
        self.assertIn("/data/references/math~10309136/submission_date", result["missing_fields"])
        self.assertEqual(dates.calls, [["hep-th/9901001", "math/0309136", "1706.03762"]])

    def test_semantic_scholar_dates_are_never_used(self):
        item = cited("2001.00001")
        item["citedPaper"]["publicationDate"] = "2019-12-31"
        item["citedPaper"]["year"] = 2019
        result, _, _ = run([page([item])], FakeDates())
        self.assertIsNone(result["data"]["references"]["2001.00001"]["submission_date"])
        self.assertEqual(result["status"], "partial")

    def test_date_lookup_is_batched(self):
        ids = [f"2001.{n:05d}" for n in range(1, 6)]
        result, _, dates = run([page([cited(i) for i in ids])], FakeDates({i: D1 for i in ids}), date_batch_size=2)
        self.assertEqual([len(c) for c in dates.calls], [2, 2, 1])
        self.assertEqual(result["status"], "success")

    def test_items_without_usable_arxiv_id_only_count_in_s2_total(self):
        items = [{"citedPaper": None}, {"citedPaper": {"paperId": "x", "externalIds": None}},
                 cited("not-an-id"), cited("2001.00001")]
        result, _, _ = run([page(items)], FakeDates({"2001.00001": D1}))
        check_contract(self, result)
        self.assertEqual(result["metrics"], {"s2_reference_count": 4, "arxiv_reference_count": 1,
                                             "complete_reference_count": 1})

    def test_invalid_external_ids_are_filtered_before_date_lookup(self):
        ids = ["2513.00001", "2500.00001", "2501.00000", "2501.1264",
               "1412.00001", "hep-th/0704001", "2001.00001"]
        result, _, dates = run([page([cited(value) for value in ids])],
                               FakeDates({"2001.00001": D1}))
        check_contract(self, result)
        self.assertEqual(result["status"], "success")
        self.assertEqual(list(result["data"]["references"]), ["2001.00001"])
        self.assertEqual(dates.calls, [["2001.00001"]])
        self.assertEqual(result["metrics"], {"s2_reference_count": 7, "arxiv_reference_count": 1,
                                             "complete_reference_count": 1})

    def test_old_display_and_canonical_ids_merge_before_date_lookup(self):
        items = [cited("math.GT/0309136v2", title="", paper_id="s2-first"),
                 cited("math/0309136", title="Known title", paper_id="s2-second")]
        result, _, dates = run([page(items)], FakeDates({"math/0309136": D1}))
        check_contract(self, result)
        self.assertEqual(result["status"], "success")
        self.assertEqual(list(result["data"]["references"]), ["math/0309136"])
        self.assertEqual(dates.calls, [["math/0309136"]])
        ref = result["data"]["references"]["math/0309136"]
        self.assertEqual((ref["title"], ref["semantic_scholar_id"]), ("Known title", "s2-first"))
        self.assertIn("/data/references/math~10309136/title", result["provenance"])

    def test_malformed_pages_are_rejected_whole(self):
        for bad in (page([cited("2001.00001"), "oops"]), {"data": [], "next": 0}, {"data": [], "next": True},
                    {"data": "x"}, {"nodata": 1}):
            with self.subTest(bad=bad):
                result, _, _ = run([bad])
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["error"]["code"], "invalid_payload")

    def test_metadata_enrichment_provenance_marks_unavailable_source_fields(self):
        result, _, _ = run([page([cited("2001.00001", title=None, authors=(), paper_id=None)])],
                           FakeDates({"2001.00001": D1}))
        self.assertEqual(result["provenance"]["metadata_enrichment"], "source_fields_unavailable")

    def test_page_limit_stops_runaway_paging(self):
        replies = [page([cited("2001.00001")], 1), page([cited("2001.00002")], 2), page([], 3)]
        result, _, _ = run(replies, FakeDates({"2001.00001": D1, "2001.00002": D1}), page_size=1, max_pages=2)
        check_contract(self, result)
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["data"]["complete"])
        self.assertEqual(result["error"]["code"], "remote_error")
        self.assertTrue(result["error"]["retryable"])
        self.assertIn("Page limit", result["error"]["message"])

    def test_adapter_dates_are_revalidated(self):
        # A shared/replaced date adapter may return junk; only valid ISO dates for requested IDs count.
        known = {"2001.00001": "2020", "2001.00002": "N/A", "2001.00003": D1, "9999.99999": D1}
        result, _, _ = run([page([cited("2001.00001"), cited("2001.00002"), cited("2001.00003")])],
                           FakeDates(known))
        check_contract(self, result)
        refs = result["data"]["references"]
        self.assertIsNone(refs["2001.00001"]["submission_date"])
        self.assertIsNone(refs["2001.00002"]["submission_date"])
        self.assertEqual(refs["2001.00003"]["submission_date"], D1)
        self.assertNotIn("9999.99999", refs)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["metrics"]["complete_reference_count"], 1)
        self.assertEqual(set(result["missing_fields"]),
                         {"/data/references/2001.00001/submission_date",
                          "/data/references/2001.00002/submission_date"})

    def test_attempts_sum_both_clients_and_start_from_zero(self):
        reader, dates = FakeReader([page([cited("2001.00001")])]), FakeDates({"2001.00001": D1})
        reader.attempts = dates.attempts = 10  # earlier calls by the same shared clients
        result = ReferencesModule(reader, dates).fetch_references(TASK, ctx())
        self.assertEqual(result["attempts"], 2)


class RawReferencesTests(unittest.TestCase):
    def test_preserves_original_fields_duplicates_invalid_ids_and_nulls(self):
        first = cited("arXiv:2001.00001v2", title="  Original title  ", authors=("  A  ",), paper_id="s2-first")
        first["citedPaper"].update({"publicationDate": "2019-12-01", "year": 2019, "venue": "Journal X"})
        first["citedPaper"]["externalIds"]["DOI"] = "10.0000/original"
        first["contexts"] = ["Original citation context"]
        items = [first, cited("2001.00001", title="Other title"), cited(None),
                 cited("2513.00001"), {"citedPaper": None}]
        original = deepcopy(items)
        result, _, dates = run([page(items)], FakeDates({"2001.00001": D1}))
        self.assertEqual(result["data"].get("raw_references"), original)
        self.assertEqual(items, original)
        self.assertEqual(result["status"], "success")
        self.assertEqual(list(result["data"]["references"]), ["2001.00001"])
        ref = result["data"]["references"]["2001.00001"]
        self.assertEqual((ref["title"], ref["authors"], ref["submission_date"]), ("Original title", ["A"], D1))
        self.assertEqual(dates.calls, [["2001.00001"]])
        self.assertEqual(result["metrics"], {"s2_reference_count": 5, "arxiv_reference_count": 1,
                                             "complete_reference_count": 1})

    def test_non_arxiv_items_are_retained_with_empty_arxiv_subset(self):
        items = [cited(None), {"citedPaper": {"paperId": "s2-no-external-id", "externalIds": None}},
                 {"citedPaper": None}]
        result, _, dates = run([page(items)])
        self.assertEqual(result["data"].get("raw_references"), items)
        self.assertEqual((result["status"], result["data"]["references"], result["data"]["complete"]),
                         ("empty", {}, True))
        self.assertEqual(result["missing_fields"], [])
        self.assertEqual(dates.calls, [])
        self.assertEqual(result["metrics"], {"s2_reference_count": 3, "arxiv_reference_count": 0,
                                             "complete_reference_count": 0})

    def test_later_page_failure_retains_only_accepted_raw_items(self):
        items = [cited(None), cited("2001.00001")]
        errors = [RemoteError("network_error", "Synthetic interrupted page", True),
                  page([cited("2001.00002"), "malformed item"])]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                result, _, _ = run([page(items, next_offset=2), error],
                                    FakeDates({"2001.00001": D1}), page_size=2)
                self.assertEqual(result["data"].get("raw_references"), items)
                self.assertEqual((result["status"], result["data"]["complete"]), ("partial", False))
                self.assertEqual(result["metrics"]["s2_reference_count"], 2)
                self.assertEqual(list(result["data"]["references"]), ["2001.00001"])

    def test_page_limit_preserves_raw_order_and_duplicates(self):
        first, second = cited(None), cited("2001.00001")
        result, _, _ = run([page([first], next_offset=1), page([second, first], next_offset=3)],
                            FakeDates({"2001.00001": D1}), max_pages=2)
        self.assertEqual(result["data"].get("raw_references"), [first, second, first])
        self.assertEqual((result["status"], result["data"]["complete"]), ("partial", False))
        self.assertEqual(result["metrics"]["s2_reference_count"], 3)

    def test_snapshots_reused_reader_objects_before_next_page(self):
        class ReusingReader:
            attempts = 0

            def __init__(self):
                self.payload = page([cited("2001.00001", title="First")], next_offset=1)

            def references_page(self, arxiv_id, offset, limit):
                self.attempts += 1
                if offset:
                    self.payload["data"][0]["citedPaper"]["externalIds"]["ArXiv"] = "2001.00002"
                    self.payload["data"][0]["citedPaper"]["title"] = "Second"
                    self.payload.pop("next")
                return self.payload

        reader = ReusingReader()
        result = ReferencesModule(reader, FakeDates({"2001.00001": D1, "2001.00002": D2})).fetch_references(TASK, ctx())
        self.assertEqual(result["data"].get("raw_references"),
                         [cited("2001.00001", title="First"),
                          {"citedPaper": {**cited("2001.00001")["citedPaper"],
                                          "title": "Second", "externalIds": {"ArXiv": "2001.00002"}}}])
        self.assertEqual(list(result["data"]["references"]), ["2001.00001", "2001.00002"])
        self.assertEqual(result["metrics"]["s2_reference_count"], 2)

    def test_raw_result_is_independent_of_reader_after_return(self):
        item = cited(None)
        result, _, _ = run([page([item])])
        raw = result["data"].get("raw_references")
        self.assertIsInstance(raw, list)
        item["citedPaper"]["authors"][0]["name"] = "Changed in reader"
        self.assertEqual(raw[0]["citedPaper"]["authors"][0]["name"], "Example Author")
        raw[0]["citedPaper"]["externalIds"]["DOI"] = "Changed in result"
        self.assertEqual(item["citedPaper"]["externalIds"]["DOI"], "10.0000/synthetic")

    def test_real_empty_page_has_empty_raw_list(self):
        result, _, _ = run([page([])])
        self.assertEqual(result["data"].get("raw_references"), [])
        self.assertEqual((result["status"], result["metrics"]["s2_reference_count"]), ("empty", 0))


class InputValidationTests(unittest.TestCase):
    def test_invalid_id_semantics_are_rejected_before_any_request(self):
        reader, dates = FakeReader([]), FakeDates()
        module = ReferencesModule(reader, dates)
        for value in ("2513.00001", "2500.00001", "2501.00000", "2501.1264", "1412.00001",
                      "0703.0001", "hep-th/9913001", "hep-th/9901000", "hep-th/0704001",
                      "hep-th/9107001", "math.GT/0309136", "２５０１.１２６４２", None, 2501.12642):
            with self.subTest(value=value), self.assertRaises(ValueError):
                module.fetch_references({"owner": TASK["owner"], "arxiv_id": value}, ctx())
        self.assertEqual(reader.calls, [])
        self.assertEqual(dates.calls, [])

    def test_bad_task_or_context_raises_before_any_request(self):
        reader, dates = FakeReader([]), FakeDates()
        module = ReferencesModule(reader, dates)
        for task in ({"owner": "someone", "arxiv_id": "2501.12642"}, {"owner": "man-nhi", "arxiv_id": "2501.12642v1"},
                     {"owner": "man-nhi", "arxiv_id": "https://arxiv.org/abs/2501.12642"},
                     {"owner": "man-nhi", "arxiv_id": ""}, {"owner": "man-nhi"}, None):
            with self.subTest(task=task), self.assertRaises(ValueError):
                module.fetch_references(task, ctx())
        for bad in ({"run_id": "a/b", "is_mock": True, "output_root": "x"},
                    {"run_id": "ok", "is_mock": "yes", "output_root": "x"}):
            with self.subTest(context=bad), self.assertRaises(ValueError):
                module.fetch_references(TASK, bad)
        self.assertEqual(reader.calls, [])

    def test_programming_errors_are_not_turned_into_empty_references(self):
        class Broken:
            attempts = 0

            def references_page(self, *args):
                raise KeyError("bug")

        with self.assertRaises(KeyError):
            ReferencesModule(Broken(), FakeDates()).fetch_references(TASK, ctx())

    def test_constructor_limits(self):
        for kwargs in ({"page_size": 0}, {"page_size": 1001}, {"max_pages": 0}, {"date_batch_size": 101}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ReferencesModule(FakeReader([]), FakeDates(), **kwargs)

    def test_scenarios_match_handoff_list(self):
        self.assertEqual(set(scenarios()), {"success", "missing_date", "empty", "page_error"})


class ParserTests(unittest.TestCase):
    def test_normalize_arxiv_id(self):
        good = {"2501.12642": "2501.12642", "2501.12642v3": "2501.12642", "arXiv:0704.0001": "0704.0001",
                " hep-th/9901001v1 ": "hep-th/9901001", "math.GT/0309136": "math/0309136"}
        for raw, expected in good.items():
            self.assertEqual(normalize_arxiv_id(raw), expected, raw)
        for bad in ("", "12345", "2501.123", "https://arxiv.org/abs/2501.12642", "hep-th/99010", None, 2501.12642,
                    "2501.12642v0", "../2501.12642"):
            self.assertIsNone(normalize_arxiv_id(bad), bad)

    def test_valid_iso_date(self):
        for ok in ("2020-01-31", "2020-01-01T10:00:00Z", "2020-01-01T10:00:00+07:00"):
            self.assertTrue(valid_iso_date(ok), ok)
        for bad in ("2020-02-30", "2020-01-01T10:00:00", "2020", "", None, "01/02/2020", 20200101):
            self.assertFalse(valid_iso_date(bad), bad)

    @staticmethod
    def feed(entries):
        body = "".join(f"<entry><id>{i}</id><published>{p}</published></entry>" for i, p in entries)
        return f'<feed xmlns="http://www.w3.org/2005/Atom">{body}</feed>'.encode()

    def test_parse_submission_dates_uses_v1_published_and_ignores_missing(self):
        body = self.feed([("http://arxiv.org/abs/hep-th/9901001v2", "1999-01-01T10:00:00Z"),
                          ("http://arxiv.org/abs/2001.00001v1", "2020-01-01T00:00:00Z")])
        dates = parse_submission_dates(body, ["hep-th/9901001", "2001.00001", "2001.00009"])
        self.assertEqual(dates, {"hep-th/9901001": "1999-01-01T10:00:00Z", "2001.00001": "2020-01-01T00:00:00Z"})

    def test_parse_submission_dates_rejects_bad_payloads(self):
        bad = [b"<html></html>", b"not xml", b"<!DOCTYPE x><feed xmlns='http://www.w3.org/2005/Atom'/>",
               self.feed([("http://arxiv.org/api/errors#incorrect_id_format", "2020-01-01T00:00:00Z")]),
               self.feed([("http://arxiv.org/abs/2001.00099", "2020-01-01T00:00:00Z")])]
        for body in bad:
            with self.subTest(body=body[:40]), self.assertRaises(RemoteError) as caught:
                parse_submission_dates(body, ["2001.00001"])
            self.assertEqual(caught.exception.info["code"], "invalid_payload")

    def test_invalid_published_value_is_treated_as_missing(self):
        body = self.feed([("http://arxiv.org/abs/2001.00001v1", "yesterday")])
        self.assertEqual(parse_submission_dates(body, ["2001.00001"]), {})


class Clock:
    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class ClientTests(unittest.TestCase):
    def test_clients_reject_invalid_ids_before_transport(self):
        s2, s2_calls, _ = self.make(SemanticScholarClient, [], api_key="")
        arxiv, arxiv_calls, _ = self.make(ArxivDateClient, [])
        for value in ("2513.00001", "2501.00000", "2501.1264", "hep-th/0704001",
                      "math.GT/0309136", "2501.12642v1"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    s2.references_page(value, 0, 10)
                with self.assertRaises(ValueError):
                    arxiv.submission_dates(["2501.12642", value])
        self.assertEqual((s2_calls, arxiv_calls), ([], []))
        self.assertEqual((s2.attempts, arxiv.attempts), (0, 0))

    def make(self, cls, replies, **kwargs):
        clock, calls, queue = Clock(), [], list(replies)

        def transport(url, timeout, headers):
            calls.append((url, dict(headers)))
            reply = queue.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply

        return cls(transport=transport, monotonic=clock.monotonic, sleep=clock.sleep, **kwargs), calls, clock

    def test_s2_url_headers_and_json(self):
        body = json.dumps(page([cited("2001.00001")])).encode()
        client, calls, _ = self.make(SemanticScholarClient, [ApiResponse(200, {}, body)], api_key="secret")
        payload = client.references_page("2501.12642", 100, 50)
        url, headers = calls[0]
        self.assertIn("/paper/ARXIV:2501.12642/references?", url)
        self.assertIn("offset=100", url)
        self.assertIn("limit=50", url)
        self.assertEqual(headers["x-api-key"], "secret")
        self.assertEqual(len(payload["data"]), 1)
        self.assertEqual(client.attempts, 1)

    def test_s2_key_comes_from_environment_and_is_optional(self):
        with mock.patch.dict("os.environ", {"S2_API_KEY": "from-env"}):
            self.assertEqual(SemanticScholarClient().headers["x-api-key"], "from-env")
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertNotIn("x-api-key", SemanticScholarClient().headers)

    def test_s2_retries_429_with_backoff_then_succeeds(self):
        ok = ApiResponse(200, {}, json.dumps(page([])).encode())
        client, _, clock = self.make(SemanticScholarClient, [ApiResponse(429, {"Retry-After": "7"}, b""), ok],
                                     api_key="")
        client.references_page("2501.12642", 0, 10)
        self.assertEqual(client.attempts, 2)
        self.assertGreaterEqual(sum(clock.sleeps), 7)

    def test_retry_after_seconds_is_not_capped(self):
        client, _, _ = self.make(SemanticScholarClient, [], api_key="")
        self.assertEqual(client._retry_after({"Retry-After": "120"}), 120)
        self.assertEqual(client._retry_after({"retry-after": "3600"}), 3600)
        ok = ApiResponse(200, {}, json.dumps(page([])).encode())
        client, _, clock = self.make(SemanticScholarClient, [ApiResponse(429, {"Retry-After": "120"}, b""), ok],
                                     api_key="")
        client.references_page("2501.12642", 0, 10)
        self.assertGreaterEqual(sum(clock.sleeps), 120)

    def test_retry_after_http_date_is_honoured_for_both_clients(self):
        # 00:18:20 GMT on 1970-01-01 is 1100 s after the epoch; the injected wall clock says 1000 s.
        header = {"Retry-After": "Thu, 01 Jan 1970 00:18:20 GMT"}
        ok_s2 = ApiResponse(200, {}, json.dumps(page([])).encode())
        client, _, clock = self.make(SemanticScholarClient, [ApiResponse(429, header, b""), ok_s2],
                                     api_key="", wall_clock=lambda: 1000.0)
        self.assertEqual(client._retry_after(header), 100)
        client.references_page("2501.12642", 0, 10)
        self.assertGreaterEqual(sum(clock.sleeps), 100)
        ok_arxiv = ApiResponse(200, {}, b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>')
        client, _, clock = self.make(ArxivDateClient, [ApiResponse(503, header, b""), ok_arxiv],
                                     wall_clock=lambda: 1000.0)
        client.submission_dates(["2001.00001"])
        self.assertEqual(client.attempts, 2)
        self.assertGreaterEqual(sum(clock.sleeps), 100)

    def test_retry_after_http_date_edge_cases(self):
        client, _, _ = self.make(SemanticScholarClient, [], api_key="", wall_clock=lambda: 1000.0)
        self.assertEqual(client._retry_after({"Retry-After": "Thu, 01 Jan 1970 00:00:09 GMT"}), 0)  # already past
        self.assertEqual(client._retry_after({"Retry-After": "Thu, 01 Jan 1970 00:18:20 -0000"}), 100)  # naive = UTC
        for bad in ("NaN", "inf", "invalid", "", "-5"):
            with self.subTest(value=bad):
                self.assertEqual(client._retry_after({"Retry-After": bad}), 0)
        self.assertEqual(client._retry_after({}), 0)

    def test_s2_404_is_not_found_without_retry(self):
        client, _, _ = self.make(SemanticScholarClient, [ApiResponse(404, {}, b"")], api_key="")
        with self.assertRaises(RemoteError) as caught:
            client.references_page("2501.12642", 0, 10)
        self.assertEqual(caught.exception.info, {"code": "not_found", "message": caught.exception.info["message"],
                                                 "retryable": False, "http_status": 404})
        self.assertEqual(client.attempts, 1)

    def test_s2_gives_up_after_three_attempts_with_rate_limited(self):
        client, _, _ = self.make(SemanticScholarClient, [ApiResponse(429, {}, b"")] * 3, api_key="")
        with self.assertRaises(RemoteError) as caught:
            client.references_page("2501.12642", 0, 10)
        self.assertEqual((caught.exception.info["code"], caught.exception.info["retryable"]), ("rate_limited", True))
        self.assertEqual(client.attempts, 3)

    def test_s2_invalid_json_and_shape(self):
        for body in (b"<html>", b"[]", b'{"data": 3}'):
            client, _, _ = self.make(SemanticScholarClient, [ApiResponse(200, {}, body)], api_key="")
            with self.subTest(body=body), self.assertRaises(RemoteError) as caught:
                client.references_page("2501.12642", 0, 10)
            self.assertEqual(caught.exception.info["code"], "invalid_payload")

    def test_s2_rejects_bad_arguments_without_a_request(self):
        client, calls, _ = self.make(SemanticScholarClient, [], api_key="")
        for args in (("https://arxiv.org/abs/2501.12642", 0, 10), ("2501.12642", -1, 10), ("2501.12642", 0, 0),
                     ("2501.12642", 0, 1001), (None, 0, 10)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                client.references_page(*args)
        self.assertEqual(calls, [])

    def test_arxiv_client_requires_three_second_delay_and_old_ids(self):
        with self.assertRaises(ValueError):
            ArxivDateClient(delay=1)
        body = ParserTests.feed([("http://arxiv.org/abs/hep-th/9901001v1", "1999-01-01T10:00:00Z")])
        client, calls, _ = self.make(ArxivDateClient, [ApiResponse(200, {}, body)])
        self.assertEqual(client.submission_dates(["hep-th/9901001"]), {"hep-th/9901001": "1999-01-01T10:00:00Z"})
        self.assertIn("id_list=hep-th%2F9901001", calls[0][0])
        for bad in ([], [None], ["2001.00001v2"]):
            with self.subTest(ids=bad), self.assertRaises(ValueError):
                client.submission_dates(bad)

    def test_arxiv_client_paces_requests(self):
        body = ParserTests.feed([])
        client, _, clock = self.make(ArxivDateClient, [ApiResponse(200, {}, body)] * 2)
        client.submission_dates(["2001.00001"])
        client.submission_dates(["2001.00002"])
        self.assertGreaterEqual(sum(clock.sleeps), 3)


class CliTests(unittest.TestCase):
    def test_cli_writes_preview_and_maps_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            reader = FakeReader([page([cited("2001.00001")])])
            dates = FakeDates({"2001.00001": D1})
            with mock.patch("arxiv_pipeline.references.cli.SemanticScholarClient", return_value=reader), \
                    mock.patch("arxiv_pipeline.references.cli.ArxivDateClient", return_value=dates):
                code = cli_main(["--owner", "man-nhi", "--arxiv-id", "2501.12642", "--run-id", "t4-test", "--output-root", tmp])
            self.assertEqual(code, 0)
            written = json.loads(Path(tmp, "preview/t4-test/man-nhi-2501-12642.references.json").read_text())
            self.assertEqual(written["status"], "success")
            self.assertFalse(written["is_mock"])
            self.assertEqual(written["data"].get("raw_references"), [cited("2001.00001")])

    def test_cli_rejects_bad_id_with_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(cli_main(["--owner", "man-nhi", "--arxiv-id", "2501.12642v2", "--output-root", tmp]), 1)

    def test_cli_requires_explicit_owner(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(SystemExit) as caught:
            cli_main(["--arxiv-id", "2501.12642", "--output-root", tmp])
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
