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
from arxiv_pipeline.metadata.api import OaiClient, _split_authors
from arxiv_pipeline.metadata.cli import main

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


@pytest.mark.parametrize("bad_id", ["2503.06914v1", "https://arxiv.org/abs/2503.06914", "", "2503/06914"])
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


