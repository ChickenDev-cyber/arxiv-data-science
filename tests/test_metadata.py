"""T2 metadata tests on synthetic arXivRaw responses (no network).

If tests/test_discovery.py imports arxiv_pipeline differently (for example it is run
with PYTHONPATH=src), delete the sys.path lines below and do the same as that file.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from arxiv_pipeline.contracts import Metadata, Result
from arxiv_pipeline.discovery.api import ApiResponse
from arxiv_pipeline.metadata import MetadataModule
from arxiv_pipeline.metadata.api import FileClient, OaiClient, _split_authors
from arxiv_pipeline.metadata.cli import main
from arxiv_pipeline.metadata.service import is_valid_arxiv_id

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "metadata"
CONTEXT = {"run_id": "test-t2", "is_mock": True, "output_root": Path("unused")}


def record(arxiv_id, versions, title="Synthetic paper", authors="Author One and Author Two", extra=""):
    version_xml = "".join(
        f'<version version="v{n}">' + (f"<date>{d}</date>" if d else "") + "<size>1kb</size></version>"
        for n, d in versions)
    return (
        '<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><GetRecord><record><metadata>'
        '<arXivRaw xmlns="http://arxiv.org/OAI/arXivRaw/">'
        f"<id>{arxiv_id}</id>{version_xml}<title>{title}</title><authors>{authors}</authors>{extra}"
        "</arXivRaw></metadata></record></GetRecord></OAI-PMH>").encode()


NOT_FOUND = (b'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">'
             b'<error code="idDoesNotExist">unknown</error></OAI-PMH>')


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


def fetch(body_or_client, arxiv_id="2503.06914", owner="hoang"):
    api = body_or_client if isinstance(body_or_client, OaiClient) else client(ApiResponse(200, {}, body_or_client))
    result = MetadataModule(api).fetch_metadata({"owner": owner, "arxiv_id": arxiv_id}, CONTEXT)
    assert list(result) == list(Result.__annotations__)
    if result["data"] is not None:
        assert list(result["data"]) == list(Metadata.__annotations__)
    json.dumps(result, allow_nan=False)
    return result


def test_single_version_success():
    r = fetch(record("2503.06914", [(1, "Mon, 3 Mar 2025 18:00:00 GMT")]))
    assert r["status"] == "success" and r["error"] is None and r["missing_fields"] == []
    assert r["owner"] == "hoang" and r["version"] is None and r["metrics"] == {}
    assert r["data"]["versions"] == [{"version": 1, "submitted_at": "2025-03-03T18:00:00Z"}]
    assert r["data"]["submission_date"] == "2025-03-03T18:00:00Z"
    assert r["data"]["revised_dates"] == [] and r["data"]["venue"] is None
    assert r["attempts"] == 1


def test_multiple_versions_are_consistent():
    r = fetch(record("2503.06914", [(2, "Wed, 5 Mar 2025 09:00:00 GMT"), (1, "Mon, 3 Mar 2025 18:00:00 GMT"),
                                    (3, "Fri, 7 Mar 2025 10:00:00 GMT")],
                     extra="<journal-ref>Synthetic Conf 2025</journal-ref>"))
    d = r["data"]
    assert r["status"] == "success" and d["versions_complete"] is True
    assert [v["version"] for v in d["versions"]] == [1, 2, 3]
    assert d["submission_date"] == d["versions"][0]["submitted_at"]
    assert d["revised_dates"] == ["2025-03-05T09:00:00Z", "2025-03-07T10:00:00Z"]
    assert d["venue"] == "Synthetic Conf 2025"


def test_missing_date_keeps_version_and_is_partial():
    r = fetch(record("2503.06914", [(1, "Mon, 3 Mar 2025 18:00:00 GMT"), (2, None)]))
    assert r["status"] == "partial" and r["error"] is None
    assert r["data"]["versions"][1] == {"version": 2, "submitted_at": None}
    assert r["data"]["versions_complete"] is False and r["data"]["revised_dates"] == []
    assert r["missing_fields"] == ["/data/versions/1/submitted_at"]


def test_gap_in_history_is_partial_without_invented_version():
    r = fetch(record("2503.06914", [(1, "Mon, 3 Mar 2025 18:00:00 GMT"), (3, "Fri, 7 Mar 2025 10:00:00 GMT")]))
    assert r["status"] == "partial"
    assert [v["version"] for v in r["data"]["versions"]] == [1, 3]
    assert r["missing_fields"] == ["/data/versions"]


def test_missing_title_and_authors_use_null():
    r = fetch(record("2503.06914", [(1, "Mon, 3 Mar 2025 18:00:00 GMT")], title="", authors=""))
    assert r["data"]["title"] is None and r["data"]["authors"] is None
    assert r["missing_fields"] == ["/data/title", "/data/authors"] and r["status"] == "partial"


def test_not_found():
    r = fetch(NOT_FOUND)
    assert r["status"] == "not_found" and r["data"] is None
    assert r["error"]["code"] == "not_found" and r["error"]["retryable"] is False


def test_network_error_is_failed_not_not_found():
    r = fetch(client(ConnectionError("down"), ConnectionError("down"), ConnectionError("down")))
    assert r["status"] == "failed" and r["data"] is None
    assert r["error"]["code"] == "network_error" and r["error"]["retryable"] is True
    assert r["attempts"] == 3


def test_flow_control_then_success_counts_retries():
    body = record("2503.06914", [(1, "Mon, 3 Mar 2025 18:00:00 GMT")])
    r = fetch(client(ApiResponse(503, {"Retry-After": "10"}, b""), ApiResponse(200, {}, body)))
    assert r["status"] == "success" and r["attempts"] == 2


def test_mismatched_id_and_dtd_are_invalid_payload():
    assert fetch(record("2503.00001", [(1, "Mon, 3 Mar 2025 18:00:00 GMT")]))["error"]["code"] == "invalid_payload"
    assert fetch(b'<!DOCTYPE x [<!ENTITY a "b">]><x/>')["error"]["code"] == "invalid_payload"


def test_old_style_id_is_accepted():
    r = fetch(record("hep-th/9901001", [(1, "Fri, 1 Jan 1999 00:00:00 GMT")]), arxiv_id="hep-th/9901001")
    assert r["status"] == "success"


@pytest.mark.parametrize("bad_id", [
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
])
def test_invalid_input_is_rejected_before_request(bad_id):
    api = client()
    with pytest.raises(ValueError):
        MetadataModule(api).fetch_metadata({"owner": "hoang", "arxiv_id": bad_id}, CONTEXT)
    assert api.attempts == 0


def test_invalid_owner_is_rejected():
    with pytest.raises(ValueError):
        MetadataModule(client()).fetch_metadata({"owner": "nobody", "arxiv_id": "2503.06914"}, CONTEXT)


def test_split_authors_keeps_order_and_drops_affiliations():
    assert _split_authors("A. One (Univ X, Lab Y), B. Two and C. Three") == ["A. One", "B. Two", "C. Three"]


@pytest.mark.parametrize("good_id", ["2503.06914", "1412.9999", "0704.0001", "1501.00001",
                                     "hep-th/9901001", "math.GT/0309136", "cond-mat/0703999"])
def test_valid_ids_are_accepted(good_id):
    assert is_valid_arxiv_id(good_id)


def test_split_authors_removes_nested_affiliations():
    value = "Author One (1), Author Two (1 and 2), Author Three (2) ((1) Institution One, (2) Institution Two)"
    assert _split_authors(value) == ["Author One", "Author Two", "Author Three"]


@pytest.mark.parametrize("value", ["Author One (Univ X, Author Two", "Author One) Univ X (, Author Two"])
def test_split_authors_with_unbalanced_brackets_is_unknown(value):
    assert _split_authors(value) is None


def test_unbalanced_author_brackets_are_not_success():
    r = fetch(record("2503.06914", [(1, "Mon, 3 Mar 2025 18:00:00 GMT")], authors="Author One (Univ X, Author Two"))
    assert r["status"] == "partial" and r["data"]["authors"] is None
    assert r["missing_fields"] == ["/data/authors"]


def test_nested_affiliations_record_is_success_with_clean_names():
    authors = "Author One (1), Author Two (1 and 2), Author Three (2) ((1) Institution One, (2) Institution Two)"
    r = fetch(record("2503.06914", [(1, "Mon, 3 Mar 2025 18:00:00 GMT")], authors=authors))
    assert r["status"] == "success"
    assert r["data"]["authors"] == ["Author One", "Author Two", "Author Three"]


def test_cli_preview_from_fixture(tmp_path):
    code = main(["--owner", "hoang", "--arxiv-id", "2503.06914", "--run-id", "cli-test",
                 "--output-root", str(tmp_path), "--fixture", str(FIXTURES / "multiple_versions.xml")])
    saved = json.loads((tmp_path / "preview" / "cli-test" / "metadata" / "2503-06914.json").read_text("utf-8"))
    assert code == 0 and saved["status"] == "success", saved["error"]
    assert saved["is_mock"] is True and saved["attempts"] == 0


def test_hand_copied_file_with_blank_line_bom_and_crlf_still_parses():
    body = record("2503.06914", [(1, "Mon, 3 Mar 2025 18:00:00 GMT")])
    body = b"\xef\xbb\xbf\r\n" + b'<?xml version="1.0" encoding="UTF-8"?>\r\n' + body.replace(b"><", b">\r\n<")
    assert fetch(body)["status"] == "success"


DATE = "Mon, 3 Mar 2025 18:00:00 GMT"


def test_provenance_names_the_real_source():
    live = fetch(record("2503.06914", [(1, DATE)]))["provenance"]
    assert live["url"].startswith("https://") and "2503.06914" in live["url"]
    saved = MetadataModule(FileClient({"2503.06914": FIXTURES / "multiple_versions.xml"})).fetch_metadata(
        {"owner": "hoang", "arxiv_id": "2503.06914"}, CONTEXT)["provenance"]
    assert saved == {"source": "saved-arXivRaw-file", "file": "multiple_versions.xml", "metadata_prefix": "arXivRaw"}


def test_file_client_without_saved_response_is_a_config_error_not_failed():
    with pytest.raises(ValueError):
        MetadataModule(FileClient({})).fetch_metadata({"owner": "hoang", "arxiv_id": "2503.06914"}, CONTEXT)


def test_deleted_oai_record_is_not_found():
    body = (b'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><GetRecord><record>'
            b'<header status="deleted"><identifier>oai:arXiv.org:2503.06914</identifier></header>'
            b'</record></GetRecord></OAI-PMH>')
    r = fetch(body)
    assert r["status"] == "not_found" and r["error"]["code"] == "not_found"


@pytest.mark.parametrize("extra", ["<title>Second title</title>", "<authors>Someone Else</authors>",
                                   "<id>2503.06914</id>"])
def test_repeated_single_value_fields_are_invalid_payload(extra):
    r = fetch(record("2503.06914", [(1, DATE)], extra=extra))
    assert r["status"] == "failed" and r["error"]["code"] == "invalid_payload"


def test_version_with_two_dates_is_invalid_payload():
    body = record("2503.06914", [(1, DATE)]).replace(b"</date>", b"</date><date>Tue, 4 Mar 2025 18:00:00 GMT</date>")
    assert fetch(body)["error"]["code"] == "invalid_payload"


def test_two_records_are_invalid_payload():
    body = record("2503.06914", [(1, DATE)])
    start, end = body.index(b"<record>"), body.index(b"</record>") + len(b"</record>")
    assert fetch(body[:end] + body[start:end] + body[end:])["error"]["code"] == "invalid_payload"


def test_title_with_nested_markup_keeps_all_text():
    r = fetch(record("2503.06914", [(1, DATE)], title="Graphs <i>and</i> trees"))
    assert r["data"]["title"] == "Graphs and trees"


def test_name_suffix_is_not_a_separate_author():
    assert _split_authors("John Smith, Jr., Jane Doe and Bob Roe III") == ["John Smith, Jr.", "Jane Doe", "Bob Roe III"]


def test_comma_inside_latex_braces_does_not_split():
    assert _split_authors("{ATLAS Collaboration, CERN}, Jane Doe") == ["{ATLAS Collaboration, CERN}", "Jane Doe"]


@pytest.mark.parametrize("value", ["Jane Doe, John Smith et al.", "Jane Doe et al", "Jane {Doe, John"])
def test_truncated_or_broken_author_lists_are_unknown(value):
    assert _split_authors(value) is None


def test_et_al_record_is_partial_not_success():
    r = fetch(record("2503.06914", [(1, DATE)], authors="Jane Doe, John Smith et al."))
    assert r["status"] == "partial" and r["missing_fields"] == ["/data/authors"]


def test_date_without_timezone_is_not_guessed():
    r = fetch(record("2503.06914", [(1, "Mon, 3 Mar 2025 18:00:00 -0000")]))
    assert r["data"]["submission_date"] is None and r["status"] == "partial"
    assert r["missing_fields"] == ["/data/submission_date", "/data/versions/0/submitted_at"]


def test_missing_v1_is_partial_and_submission_date_stays_null():
    r = fetch(record("2503.06914", [(2, DATE)]))
    assert r["status"] == "partial" and r["data"]["submission_date"] is None
    assert r["data"]["revised_dates"] == ["2025-03-03T18:00:00Z"]
    assert r["missing_fields"] == ["/data/submission_date", "/data/versions"]


def test_no_versions_is_partial_with_empty_list():
    r = fetch(record("2503.06914", []))
    assert r["status"] == "partial" and r["data"]["versions"] == [] and r["data"]["versions_complete"] is False
    assert r["missing_fields"] == ["/data/versions"]  # no fake v1, no pointer to a key that has no basis


def test_html_error_page_is_invalid_payload_not_success():
    r = fetch(b"<html><body>Service temporarily unavailable</body></html>")
    assert r["status"] == "failed" and r["error"]["code"] == "invalid_payload"


def test_other_oai_error_is_remote_error_not_not_found():
    body = (b'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">'
            b'<error code="badArgument">bad</error></OAI-PMH>')
    r = fetch(body)
    assert r["status"] == "failed" and r["error"]["code"] == "remote_error"


def test_http_404_is_failed_not_not_found():
    r = fetch(client(ApiResponse(404, {}, b"")))
    assert r["status"] == "failed" and r["error"] == {
        "code": "remote_error", "message": "arXiv OAI returned HTTP 404", "retryable": False, "http_status": 404}
    assert r["attempts"] == 1
