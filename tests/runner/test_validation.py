from copy import deepcopy
from pathlib import Path
import unittest

from arxiv_pipeline.validation import validate_result, validate_for_task
from arxiv_pipeline.runner.models import paper_spec
from .helpers import PAPER, metadata_result, references_result


class ValidationTests(unittest.TestCase):
    def test_valid_and_partial_and_raw_only(self):
        for r in (metadata_result(), metadata_result(True), references_result()):
            self.assertEqual(validate_result(r), [])

    def test_rejects_envelope_errors_without_throwing(self):
        for key, value in (("schema_version", "1.1"), ("attempts", True),
                           ("duration_seconds", float("nan")), ("owner", "nobody"),
                           ("version", 1), ("status", "empty"), ("provenance", {"x": 1}),
                           ("started_at", "2026-01-01T00:00:00"), ("finished_at", "2020-01-01T00:00:00Z"),
                           ("missing_fields", ["/no-such-key"])):
            with self.subTest(key=key):
                bad = metadata_result(); bad[key] = value
                self.assertTrue(validate_result(bad))
        for bad in (None, [], {}, {"data": 3}):
            self.assertTrue(validate_result(bad))

    def test_payload_errors_and_identity(self):
        for value in (None, [], 3, {"arxiv_id": "wrong"}):
            r = metadata_result(); r["data"] = value
            self.assertTrue(validate_result(r))
        r = references_result(); r["metrics"]["s2_reference_count"] = 0
        self.assertTrue(validate_result(r))
        ctx = {"run_id": "mock-demo", "is_mock": True, "output_root": Path("/tmp/test")}
        self.assertEqual(validate_for_task(metadata_result(), paper_spec(PAPER, "metadata"), ctx), [])
        for key, value in (("owner", "khang"), ("arxiv_id", "2504.17524"),
                           ("run_id", "other"), ("is_mock", False)):
            r = metadata_result(); r[key] = value
            self.assertTrue(validate_for_task(r, paper_spec(PAPER, "metadata"), ctx))

    def test_date_reversal_is_valid_partial(self):
        r = metadata_result(); r["status"] = "partial"; r["data"]["versions_complete"] = False
        r["data"]["versions"][1]["submitted_at"] = "2025-03-31"
        r["data"]["revised_dates"] = ["2025-03-31"]
        self.assertEqual(validate_result(r), [])

    def test_whitespace_metadata_cannot_be_success(self):
        for field,value in (("title","   "),("authors",[" "])):
            r=metadata_result();r['data'][field]=value
            with self.subTest(field=field):self.assertTrue(validate_result(r))

    def test_failed_data_null_may_have_unknown_empty_metrics(self):
        for step in ('sources','references'):
            for status in ('failed','not_found'):
                r=metadata_result();r.update(step=step,status=status,data=None,metrics={},
                    version=1 if step=='sources' else None,error={
                    'code':'not_found' if status=='not_found' else 'network_error',
                    'message':'synthetic failure','retryable':False,'http_status':404 if status=='not_found' else None})
                with self.subTest(step=step,status=status):self.assertEqual(validate_result(r),[])
