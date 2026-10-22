"""Offline tests for T2 metadata on synthetic arXivRaw responses (no network).

Run from the repository root: PYTHONPATH=src python -m unittest discover -s tests -v
"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from arxiv_pipeline.contracts import Metadata, Result
from arxiv_pipeline.discovery.api import ApiResponse
from arxiv_pipeline.metadata import MetadataModule
from arxiv_pipeline.metadata.api import FileClient, OaiClient, _split_authors
from arxiv_pipeline.metadata.cli import main
from arxiv_pipeline.metadata.service import is_valid_arxiv_id

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "metadata"
CONTEXT = {"run_id": "test-t2", "is_mock": True, "output_root": Path("unused")}
DATE = "Mon, 3 Mar 2025 18:00:00 GMT"


def request(arxiv_id):
    return f'<request verb="GetRecord" identifier="oai:arXiv.org:{arxiv_id}" metadataPrefix="arXivRaw">x</request>'


def record(arxiv_id, versions, title="Synthetic paper", authors="Author One and Author Two", extra="",
           requested=None, header=None):
    """Synthetic GetRecord; requested/header default to arxiv_id (override to test mismatches)."""
    version_xml = "".join(
        f'<version version="v{n}">' + (f"<date>{d}</date>" if d else "") + "<size>1kb</size></version>"
        for n, d in versions)
    header_xml = f"<header><identifier>oai:arXiv.org:{header or arxiv_id}</identifier></header>"
    return (
        '<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">' + request(requested or arxiv_id) +
        f"<GetRecord><record>{header_xml}<metadata>"
        '<arXivRaw xmlns="http://arxiv.org/OAI/arXivRaw/">'
        f"<id>{arxiv_id}</id>{version_xml}<title>{title}</title><authors>{authors}</authors>{extra}"
        "</arXivRaw></metadata></record></GetRecord></OAI-PMH>").encode()


def deleted(header_id, requested="2503.06914", header_xml=None):
    header_xml = header_xml or (f'<header status="deleted"><identifier>oai:arXiv.org:{header_id}'
                                "</identifier></header>")
    return ('<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">' + request(requested) +
            f"<GetRecord><record>{header_xml}</record></GetRecord></OAI-PMH>").encode()


def oai_error(code, requested="2503.06914"):
    request_xml = request(requested) if requested else '<request verb="GetRecord">x</request>'
    return (f'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">{request_xml}'
            f'<error code="{code}">synthetic</error></OAI-PMH>').encode()


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)

    def __call__(self, url, timeout, headers):
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def client(*responses):
    clock = [0.0]
    return OaiClient(transport=FakeTransport(*responses), monotonic=lambda: clock[0],
                     sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds))


class MetadataTestCase(unittest.TestCase):
    def fetch(self, body_or_client, arxiv_id="2503.06914", owner="hoang"):
        api = body_or_client if isinstance(body_or_client, OaiClient) else client(ApiResponse(200, {}, body_or_client))
        result = MetadataModule(api).fetch_metadata({"owner": owner, "arxiv_id": arxiv_id}, CONTEXT)
        self.assertEqual(list(result), list(Result.__annotations__))
        if result["data"] is not None:
            self.assertEqual(list(result["data"]), list(Metadata.__annotations__))
        json.dumps(result, allow_nan=False)
        return result


class StatusAndVersionTests(MetadataTestCase):
    def test_single_version_success(self):
        r = self.fetch(record("2503.06914", [(1, DATE)]))
        self.assertEqual((r["status"], r["error"], r["missing_fields"]), ("success", None, []))
        self.assertEqual((r["owner"], r["version"], r["metrics"]), ("hoang", None, {}))
        self.assertEqual(r["data"]["versions"], [{"version": 1, "submitted_at": "2025-03-03T18:00:00Z"}])
        self.assertEqual(r["data"]["submission_date"], "2025-03-03T18:00:00Z")
        self.assertEqual((r["data"]["revised_dates"], r["data"]["venue"]), ([], None))
        self.assertEqual(r["attempts"], 1)

    def test_multiple_versions_are_consistent(self):
        r = self.fetch(record("2503.06914", [(2, "Wed, 5 Mar 2025 09:00:00 GMT"), (1, DATE),
                                             (3, "Fri, 7 Mar 2025 10:00:00 GMT")],
                              extra="<journal-ref>Synthetic Conf 2025</journal-ref>"))
        d = r["data"]
        self.assertEqual(r["status"], "success")
        self.assertIs(d["versions_complete"], True)
        self.assertEqual([v["version"] for v in d["versions"]], [1, 2, 3])
        self.assertEqual(d["submission_date"], d["versions"][0]["submitted_at"])
        self.assertEqual(d["revised_dates"], ["2025-03-05T09:00:00Z", "2025-03-07T10:00:00Z"])
        self.assertEqual(d["venue"], "Synthetic Conf 2025")

    def test_reversed_version_dates_are_partial_and_preserved(self):
        r = self.fetch(record("2503.06914", [(2, DATE), (1, "Wed, 5 Mar 2025 18:00:00 GMT")]))
        self.assertEqual(r["status"], "partial")
        self.assertIs(r["data"]["versions_complete"], False)
        self.assertEqual(r["data"]["versions"], [
            {"version": 1, "submitted_at": "2025-03-05T18:00:00Z"},
            {"version": 2, "submitted_at": "2025-03-03T18:00:00Z"},
        ])
        self.assertEqual(r["data"]["submission_date"], "2025-03-05T18:00:00Z")
        self.assertEqual(r["data"]["revised_dates"], ["2025-03-03T18:00:00Z"])
        self.assertEqual(r["missing_fields"], [])
        self.assertIsNone(r["error"])
        warning = r["provenance"]["version_date_order"]
        for detail in ("v2", "v1", "2025-03-03T18:00:00Z", "2025-03-05T18:00:00Z"):
            self.assertIn(detail, warning)

    def test_date_order_is_checked_between_later_versions(self):
        r = self.fetch(record("2503.06914", [(1, DATE), (2, "Fri, 7 Mar 2025 18:00:00 GMT"),
                                             (3, "Wed, 5 Mar 2025 18:00:00 GMT")]))
        self.assertEqual(r["status"], "partial")
        self.assertIs(r["data"]["versions_complete"], False)
        self.assertEqual(r["data"]["revised_dates"], ["2025-03-07T18:00:00Z", "2025-03-05T18:00:00Z"])
        self.assertEqual(r["missing_fields"], [])
        for version in ("v3", "v2"):
            self.assertIn(version, r["provenance"]["version_date_order"])

    def test_date_order_checks_known_dates_across_missing_dates(self):
        r = self.fetch(record("2503.06914", [(1, "Wed, 5 Mar 2025 18:00:00 GMT"), (2, None), (3, DATE)]))
        self.assertEqual(r["status"], "partial")
        self.assertIs(r["data"]["versions_complete"], False)
        self.assertEqual(r["data"]["versions"][1], {"version": 2, "submitted_at": None})
        self.assertEqual(r["missing_fields"], ["/data/versions/1/submitted_at"])
        for version in ("v3", "v1"):
            self.assertIn(version, r["provenance"]["version_date_order"])

    def test_equal_version_dates_are_success(self):
        r = self.fetch(record("2503.06914", [(1, DATE), (2, DATE)]))
        self.assertEqual(r["status"], "success")
        self.assertIs(r["data"]["versions_complete"], True)
        self.assertEqual(r["missing_fields"], [])
        self.assertNotIn("version_date_order", r["provenance"])

    def test_version_dates_compare_utc_instants(self):
        cases = [
            ("Mon, 3 Mar 2025 18:00:00 +0700", "Mon, 3 Mar 2025 12:00:00 +0000", "success"),
            ("Mon, 3 Mar 2025 12:00:00 +0000", "Mon, 3 Mar 2025 18:00:00 +0700", "partial"),
            ("Mon, 3 Mar 2025 11:00:00 +0000", "Mon, 3 Mar 2025 18:00:00 +0700", "success"),
        ]
        for first, second, status in cases:
            with self.subTest(first=first, second=second):
                r = self.fetch(record("2503.06914", [(1, first), (2, second)]))
                self.assertEqual(r["status"], status)
                self.assertEqual(r["data"]["versions_complete"], status == "success")
                self.assertEqual(r["missing_fields"], [])
                self.assertEqual("version_date_order" in r["provenance"], status == "partial")

    def test_date_warning_does_not_leak_through_shared_client_provenance(self):
        api = client(
            ApiResponse(200, {}, record("2503.06914", [(1, "Wed, 5 Mar 2025 18:00:00 GMT"), (2, DATE)])),
            ApiResponse(200, {}, record("2503.06914", [(1, DATE)])),
        )
        shared_provenance = {"source": "shared-client"}
        api.provenance = lambda arxiv_id: shared_provenance
        first, second = self.fetch(api), self.fetch(api)
        self.assertIn("version_date_order", first["provenance"])
        self.assertEqual(shared_provenance, {"source": "shared-client"})
        self.assertNotIn("version_date_order", second["provenance"])

    def test_missing_date_keeps_version_and_is_partial(self):
        r = self.fetch(record("2503.06914", [(1, DATE), (2, None)]))
        self.assertEqual((r["status"], r["error"]), ("partial", None))
        self.assertEqual(r["data"]["versions"][1], {"version": 2, "submitted_at": None})
        self.assertIs(r["data"]["versions_complete"], False)
        self.assertEqual(r["data"]["revised_dates"], [])
        self.assertEqual(r["missing_fields"], ["/data/versions/1/submitted_at"])

    def test_gap_in_history_is_partial_without_invented_version(self):
        r = self.fetch(record("2503.06914", [(1, DATE), (3, "Fri, 7 Mar 2025 10:00:00 GMT")]))
        self.assertEqual(r["status"], "partial")
        self.assertEqual([v["version"] for v in r["data"]["versions"]], [1, 3])
        self.assertEqual(r["missing_fields"], ["/data/versions"])

    def test_missing_v1_is_partial_and_submission_date_stays_null(self):
        r = self.fetch(record("2503.06914", [(2, DATE)]))
        self.assertEqual(r["status"], "partial")
        self.assertIsNone(r["data"]["submission_date"])
        self.assertEqual(r["data"]["revised_dates"], ["2025-03-03T18:00:00Z"])
        self.assertEqual(r["missing_fields"], ["/data/submission_date", "/data/versions"])

    def test_no_versions_is_partial_with_empty_list(self):
        r = self.fetch(record("2503.06914", []))
        self.assertEqual((r["status"], r["data"]["versions"]), ("partial", []))
        self.assertIs(r["data"]["versions_complete"], False)
        self.assertEqual(r["missing_fields"], ["/data/versions"])

    def test_missing_title_and_authors_use_null(self):
        r = self.fetch(record("2503.06914", [(1, DATE)], title="", authors=""))
        self.assertIsNone(r["data"]["title"])
        self.assertIsNone(r["data"]["authors"])
        self.assertEqual((r["status"], r["missing_fields"]), ("partial", ["/data/title", "/data/authors"]))

    def test_date_without_timezone_is_not_guessed(self):
        r = self.fetch(record("2503.06914", [(1, "Mon, 3 Mar 2025 18:00:00 -0000")]))
        self.assertIsNone(r["data"]["submission_date"])
        self.assertEqual(r["status"], "partial")
        self.assertEqual(r["missing_fields"], ["/data/submission_date", "/data/versions/0/submitted_at"])

    def test_title_with_nested_markup_keeps_all_text(self):
        r = self.fetch(record("2503.06914", [(1, DATE)], title="Graphs <i>and</i> trees"))
        self.assertEqual(r["data"]["title"], "Graphs and trees")


class RemoteAndPayloadTests(MetadataTestCase):
    def test_not_found(self):
        r = self.fetch(oai_error("idDoesNotExist"))
        self.assertEqual((r["status"], r["data"]), ("not_found", None))
        self.assertEqual((r["error"]["code"], r["error"]["retryable"]), ("not_found", False))

    def test_deleted_oai_record_is_not_found(self):
        r = self.fetch(deleted("2503.06914"))
        self.assertEqual((r["status"], r["error"]["code"]), ("not_found", "not_found"))

    def test_not_found_only_when_response_names_the_requested_id(self):
        cases = {
            "deleted record of another paper": deleted("2503.99999"),
            "deleted record without identifier": deleted(None, header_xml='<header status="deleted"></header>'),
            "deleted record without header": deleted(None, header_xml="<about/>"),
            "deleted record, request names another paper": deleted("2503.06914", requested="2503.99999"),
            "idDoesNotExist for another paper": oai_error("idDoesNotExist", requested="2503.99999"),
            "idDoesNotExist without request identifier": oai_error("idDoesNotExist", requested=None),
            "idDoesNotExist without request element": oai_error("idDoesNotExist").replace(
                request("2503.06914").encode(), b""),
        }
        for name, payload in cases.items():
            with self.subTest(name):
                r = self.fetch(payload)
                self.assertEqual((r["status"], r["error"]["code"]), ("failed", "invalid_payload"))

    def test_active_record_must_name_the_requested_id(self):
        cases = {
            "header names another paper": record("2503.06914", [(1, DATE)], header="2503.99999"),
            "request names another paper": record("2503.06914", [(1, DATE)], requested="2503.99999"),
            "header without identifier": record("2503.06914", [(1, DATE)]).replace(
                b"<identifier>oai:arXiv.org:2503.06914</identifier>", b""),
        }
        for name, payload in cases.items():
            with self.subTest(name):
                r = self.fetch(payload)
                self.assertEqual((r["status"], r["error"]["code"]), ("failed", "invalid_payload"))

    def test_other_oai_error_is_remote_error_not_not_found(self):
        r = self.fetch(oai_error("badArgument"))
        self.assertEqual((r["status"], r["error"]["code"]), ("failed", "remote_error"))

    def test_network_error_is_failed_not_not_found(self):
        r = self.fetch(client(ConnectionError("down"), ConnectionError("down"), ConnectionError("down")))
        self.assertEqual((r["status"], r["data"]), ("failed", None))
        self.assertEqual((r["error"]["code"], r["error"]["retryable"]), ("network_error", True))
        self.assertEqual(r["attempts"], 3)

    def test_http_404_is_failed_not_not_found(self):
        r = self.fetch(client(ApiResponse(404, {}, b"")))
        self.assertEqual(r["status"], "failed")
        self.assertEqual(r["error"], {"code": "remote_error", "message": "arXiv OAI returned HTTP 404",
                                      "retryable": False, "http_status": 404})
        self.assertEqual(r["attempts"], 1)

    def test_http_error_codes_match_t1(self):
        for status, code, retryable in [(429, "rate_limited", True), (503, "remote_error", True),
                                        (500, "remote_error", True), (400, "remote_error", False)]:
            with self.subTest(status):
                r = self.fetch(client(*[ApiResponse(status, {}, b"")] * 3))
                self.assertEqual((r["error"]["code"], r["error"]["retryable"], r["error"]["http_status"]),
                                 (code, retryable, status))

    def test_flow_control_then_success_counts_retries(self):
        body = record("2503.06914", [(1, DATE)])
        r = self.fetch(client(ApiResponse(503, {"Retry-After": "10"}, b""), ApiResponse(200, {}, body)))
        self.assertEqual((r["status"], r["attempts"]), ("success", 2))

    def test_invalid_payloads_are_failed(self):
        body = record("2503.06914", [(1, DATE)])
        start, end = body.index(b"<record>"), body.index(b"</record>") + len(b"</record>")
        cases = {
            "mismatched id": record("2503.00001", [(1, DATE)]),
            "dtd": b'<!DOCTYPE x [<!ENTITY a "b">]><x/>',
            "html page": b"<html><body>Service temporarily unavailable</body></html>",
            "empty": b"",
            "two titles": record("2503.06914", [(1, DATE)], extra="<title>Second title</title>"),
            "two author fields": record("2503.06914", [(1, DATE)], extra="<authors>Someone Else</authors>"),
            "two ids": record("2503.06914", [(1, DATE)], extra="<id>2503.06914</id>"),
            "two dates": body.replace(b"</date>", b"</date><date>Tue, 4 Mar 2025 18:00:00 GMT</date>"),
            "two records": body[:end] + body[start:end] + body[end:],
        }
        for name, payload in cases.items():
            with self.subTest(name):
                r = self.fetch(payload)
                self.assertEqual((r["status"], r["error"]["code"]), ("failed", "invalid_payload"))

    def test_hand_copied_file_with_blank_line_bom_and_crlf_still_parses(self):
        body = record("2503.06914", [(1, DATE)])
        body = b"\xef\xbb\xbf\r\n" + b'<?xml version="1.0" encoding="UTF-8"?>\r\n' + body.replace(b"><", b">\r\n<")
        self.assertEqual(self.fetch(body)["status"], "success")

    def test_old_style_id_is_accepted(self):
        r = self.fetch(record("hep-th/9901001", [(1, "Fri, 1 Jan 1999 00:00:00 GMT")]), arxiv_id="hep-th/9901001")
        self.assertEqual(r["status"], "success")


class InputTests(unittest.TestCase):
    def test_invalid_ids_are_rejected_before_request(self):
        bad_ids = [
            "2503.06914v1", "https://arxiv.org/abs/2503.06914", "", "2503/06914",
            "2513.00001",      # month 13
            "2500.00001",      # month 0
            "2503.00000",      # sequence number 0
            "2503.6914",       # IDs from 2015 need 5 digits
            "1412.00001",      # IDs before 2015 have 4 digits
            "0703.0001",       # new scheme starts at 0704
            "hep-th/9913001",  # old ID with month 13
            "hep-th/9901000",  # old ID with number 0
            "hep-th/0704001",  # old scheme ends at 0703
            "hep-th/9107001",  # old scheme starts at 9108
            "HEP-TH/9901001",  # archive must be lowercase
            "math.GT/0309136", # subject class is not part of the canonical old ID
        ]
        for bad_id in bad_ids:
            with self.subTest(bad_id):
                api = client()
                with self.assertRaises(ValueError):
                    MetadataModule(api).fetch_metadata({"owner": "hoang", "arxiv_id": bad_id}, CONTEXT)
                self.assertEqual(api.attempts, 0)

    def test_valid_ids_are_accepted(self):
        for good_id in ["2503.06914", "1412.9999", "0704.0001", "1501.00001",
                        "hep-th/9901001", "math/0309136", "cond-mat/0703999"]:
            with self.subTest(good_id):
                self.assertTrue(is_valid_arxiv_id(good_id))

    def test_invalid_owner_is_rejected(self):
        with self.assertRaises(ValueError):
            MetadataModule(client()).fetch_metadata({"owner": "nobody", "arxiv_id": "2503.06914"}, CONTEXT)

    def test_file_client_without_saved_response_is_a_config_error_not_failed(self):
        with self.assertRaises(ValueError):
            MetadataModule(FileClient({})).fetch_metadata({"owner": "hoang", "arxiv_id": "2503.06914"}, CONTEXT)


class AuthorTests(MetadataTestCase):
    def test_split_authors(self):
        cases = {
            "A. One (Univ X, Lab Y), B. Two and C. Three": ["A. One", "B. Two", "C. Three"],
            "Author One (1), Author Two (1 and 2), Author Three (2) ((1) Institution One, (2) Institution Two)":
                ["Author One", "Author Two", "Author Three"],
            "John Smith, Jr., Jane Doe and Bob Roe III": ["John Smith, Jr.", "Jane Doe", "Bob Roe III"],
            "{ATLAS Collaboration, CERN}, Jane Doe": ["{ATLAS Collaboration, CERN}", "Jane Doe"],
            "ABCD Collaboration: Author One, Author Two, Author Three":
                ["ABCD Collaboration", "Author One", "Author Two", "Author Three"],
            "Author One, Author Two, for the ABCD Collaboration": ["Author One", "Author Two", "ABCD Collaboration"],
            "Author One, Author Two (the ABCD Collaboration)": ["Author One", "Author Two"],
            "Author One and the ABCD Consortium": ["Author One", "ABCD Consortium"],
            "Bill Gates Jr, Fred Bloggs IV and John von Neumann": ["Bill Gates Jr", "Fred Bloggs IV", "John von Neumann"],
            "E. L. Grossman, T. Zhou, E. Ben-Naim": ["E. L. Grossman", "T. Zhou", "E. Ben-Naim"],
            "Michael Dine, Yosef Nir and Yuri Shirman": ["Michael Dine", "Yosef Nir", "Yuri Shirman"],
            'J. M\\"uller, {\\ss}ome One': ['J. M\\"uller', "{\\ss}ome One"],
        }
        for value, expected in cases.items():
            with self.subTest(value):
                self.assertEqual(_split_authors(value), expected)

    def test_unsafe_author_lists_are_unknown(self):
        for value in ["Author One (Univ X, Author Two", "Author One) Univ X (, Author Two",
                      "Author One; Author Two", "Team X: Author One, Author Two",
                      "Jane Doe, John Smith et al.", "Jane Doe et al", "Jane {Doe, John"]:
            with self.subTest(value):
                self.assertIsNone(_split_authors(value))

    def test_unsafe_author_list_is_partial_not_success(self):
        for value in ["Author One (Univ X, Author Two", "Jane Doe, John Smith et al."]:
            with self.subTest(value):
                r = self.fetch(record("2503.06914", [(1, DATE)], authors=value))
                self.assertEqual((r["status"], r["missing_fields"]), ("partial", ["/data/authors"]))
                self.assertIsNone(r["data"]["authors"])

    def test_nested_affiliations_record_is_success_with_clean_names(self):
        authors = "Author One (1), Author Two (1 and 2), Author Three (2) ((1) Institution One, (2) Institution Two)"
        r = self.fetch(record("2503.06914", [(1, DATE)], authors=authors))
        self.assertEqual(r["status"], "success")
        self.assertEqual(r["data"]["authors"], ["Author One", "Author Two", "Author Three"])


class ProvenanceAndCliTests(MetadataTestCase):
    def test_provenance_names_the_real_source(self):
        live = self.fetch(record("2503.06914", [(1, DATE)]))["provenance"]
        self.assertTrue(live["url"].startswith("https://") and "2503.06914" in live["url"])
        self.assertEqual(live["abstract_page"], "https://arxiv.org/abs/2503.06914")
        saved = MetadataModule(FileClient({"2503.06914": FIXTURES / "multiple_versions.xml"})).fetch_metadata(
            {"owner": "hoang", "arxiv_id": "2503.06914"}, CONTEXT)["provenance"]
        self.assertEqual(saved, {"source": "saved-arXivRaw-file", "file": "multiple_versions.xml",
                                 "metadata_prefix": "arXivRaw"})

    def run_cli(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_cli_writes_preview_for_each_fixture(self):
        cases = [("single_version.xml", "2503.07000", "success", 0),
                 ("multiple_versions.xml", "2503.06914", "success", 0),
                 ("missing_date.xml", "2503.07001", "partial", 2),
                 ("not_found.xml", "2503.09999", "not_found", 3)]
        for fixture, arxiv_id, status, exit_code in cases:
            with self.subTest(fixture), tempfile.TemporaryDirectory() as root:
                code, stdout, stderr = self.run_cli(
                    "--owner", "hoang", "--arxiv-id", arxiv_id, "--run-id", "cli-test",
                    "--output-root", root, "--fixture", str(FIXTURES / fixture))
                saved = json.loads((Path(root) / "preview" / "cli-test" / "metadata" /
                                    f"{arxiv_id.replace('.', '-')}.json").read_text("utf-8"))
                self.assertEqual((code, saved["status"]), (exit_code, status), saved["error"])
                self.assertIs(saved["is_mock"], True)
                self.assertEqual(saved["attempts"], 0)
                self.assertEqual(json.loads(stdout)["status"], status)
                self.assertIn("saved file", stderr)

    def test_cli_rejects_invalid_id_without_result(self):
        with tempfile.TemporaryDirectory() as root:
            code, stdout, stderr = self.run_cli("--owner", "hoang", "--arxiv-id", "2513.00001",
                                                "--output-root", root)
            self.assertEqual((code, stdout), (1, ""))
            self.assertIn("T2:", stderr)
            self.assertFalse((Path(root) / "preview").exists())


if __name__ == "__main__":
    unittest.main()

