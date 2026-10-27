from copy import deepcopy
import json
import unittest

from arxiv_pipeline.json_utils import canonical
from arxiv_pipeline.validation.pipeline_contracts import SUMMARY_RATES, validate_pipeline_document

NOW = "2026-10-27T00:00:00Z"
RANGE = {"owner": "huynh-nhi", "start_month": "2504", "start_id": 1, "end_month": "2504",
         "end_id": 10, "inclusive": True}


def discovery_checkpoint():
    payload = {"owner": "huynh-nhi", "range_config": dict(RANGE), "max_candidates": None,
               "batch_size": 3, "offset": 0, "candidate_ids": ["2504.00001", "2504.00002", "2504.00003"],
               "found_ids": ["2504.00001"], "excluded_ids": ["2504.00002"], "unresolved_ids": ["2504.00003"],
               "error": {"code": "network_error", "message": "timeout", "retryable": True, "http_status": None}}
    key = canonical(["huynh-nhi", RANGE, None, 3, 0])
    return {"format_version": 1, "namespace": "discovery_batches", "key": key,
            "payload": payload, "updated_at": NOW}


def references_checkpoint():
    pages = [{"offset": 0, "next_offset": 2, "raw_items": [{}, {}], "fetched_at": NOW},
             {"offset": 2, "next_offset": None, "raw_items": [{}], "fetched_at": NOW}]
    payload = {"owner": "man-nhi", "arxiv_id": "2504.17523", "page_size": 2,
               "fields": "title,externalIds", "pages": pages}
    return {"format_version": 1, "namespace": "references", "key": canonical(["man-nhi", "2504.17523"]),
            "payload": payload, "updated_at": NOW}


def date_checkpoint():
    payload = {"arxiv_id": "2504.17523", "submission_date": "2025-04-24",
               "source": "https://export.arxiv.org/api/query", "fetched_at": NOW}
    return {"format_version": 1, "namespace": "arxiv_dates", "key": '["2504.17523"]',
            "payload": payload, "updated_at": NOW}


def rate_checkpoint():
    payload = {"group": "arxiv", "next_allowed_at": "2026-10-27T00:00:03Z",
               "recent_requests_at": ["2026-10-26T23:59:57Z", NOW], "consecutive_429": 0}
    return {"format_version": 1, "namespace": "rate_limits", "key": '["arxiv"]',
            "payload": payload, "updated_at": NOW}


def snapshot():
    return {"format_version": 1, "kind": "arxiv-snapshot", "snapshot_id": "nhi-2026-10-27",
            "schema_version": "1.2", "run_id": "nhi-full", "is_mock": True, "owners": ["huynh-nhi"],
            "created_at": NOW, "exporter_commit": "a" * 40, "producer_commits": ["b" * 40],
            "unknown_provenance_tasks": 0, "config_fingerprint": "c" * 64,
            "origin_output_root": "D:/tmp/arxiv",
            "files": [{"path": "raw/huynh-nhi/2504-17523/metadata.json", "size_bytes": 20, "sha256": "d" * 64}]}


def member(snapshot_id, owners):
    return {"snapshot_id": snapshot_id, "run_id": snapshot_id + "-run", "owners": owners,
            "exporter_commit": "a" * 40, "root": "runs/" + snapshot_id,
            "manifest_path": "runs/" + snapshot_id + "/manifest.json"}


def collection():
    return {"format_version": 1, "kind": "arxiv-collection", "collection_id": "group-1",
            "schema_version": "1.2", "is_mock": True, "created_at": NOW,
            "members": [member("nhi", ["huynh-nhi"]), member("trong", ["trong", "khang"])]}


def summary(**counts):
    """Counts as given; rates and means derived the way schema spec section 11 defines them."""
    s = {"papers_discovered": 0, "metadata_complete_papers": 0, "papers_complete": 0,
         "source_versions_expected": 0, "source_versions_success": 0,
         "source_versions_unsupported": 0, "source_versions_unresolved": 0,
         "references_fully_fetched_papers": 0, "references_metadata_complete_papers": 0,
         "references_arxiv_total": 0, "references_complete_total": 0, "source_size_sample_count": 0}
    s.update(counts)
    for name, (numerator, denominator) in SUMMARY_RATES.items():
        s[name] = s[numerator] / s[denominator] if s[denominator] else None
    mean = 100.0 if s["source_size_sample_count"] else None
    s.update(mean_extracted_bytes_before_filter=mean, mean_retained_source_bytes=mean)
    return s


NHI_PAPERS = [{"owner": "huynh-nhi", "arxiv_id": f"2409.{n:05d}"} for n in range(3594, 3598)]


def statistics():
    scope = {"ranges": [], "paper_tasks": list(NHI_PAPERS), "discovery_complete": False,
             "versions_complete": False, "owner_count": 1, "paper_count": 4}
    counts = {"papers_discovered": 4, "papers_complete": 2, "metadata_complete_papers": 3,
              "source_versions_expected": 5, "source_versions_success": 3,
              "source_versions_unsupported": 1, "source_versions_unresolved": 1,
              "source_size_sample_count": 2}
    return {"format_version": 1, "kind": "arxiv-collection-statistics", "schema_version": "1.2",
            "collection_id": "group-1", "is_mock": True, "sources": [member("nhi", ["huynh-nhi"])],
            "scope": scope, "missing_owners": ["trong"],
            "summary": summary(**counts), "by_owner": {"huynh-nhi": summary(**counts)}}


class PipelineDocumentValidatorTests(unittest.TestCase):
    def assertValid(self, document, kind):
        self.assertEqual(validate_pipeline_document(document, kind), [])

    def assertInvalid(self, document, kind):
        errors = validate_pipeline_document(document, kind)
        self.assertTrue(errors)
        self.assertTrue(all(isinstance(e, str) for e in errors))

    def test_valid_documents_have_no_problems(self):
        for record in (discovery_checkpoint(), references_checkpoint(), date_checkpoint(), rate_checkpoint()):
            with self.subTest(namespace=record["namespace"]):
                self.assertValid(record, "checkpoint")
        self.assertValid(snapshot(), "snapshot")
        self.assertValid(collection(), "collection")
        self.assertValid(statistics(), "collection-statistics")

    def test_unknown_kind_is_programming_error(self):
        with self.assertRaises(ValueError):
            validate_pipeline_document(snapshot(), "manifest")

    def test_rejects_non_json_and_structure_errors(self):
        for bad in (None, [], {}, {**date_checkpoint(), "extra": 1},
                    {**date_checkpoint(), "format_version": True},
                    {**date_checkpoint(), "namespace": "unknown"},
                    {**date_checkpoint(), "updated_at": float("nan")}):
            with self.subTest(bad=bad):
                self.assertInvalid(bad, "checkpoint")

    def test_payload_must_match_its_namespace(self):
        record = date_checkpoint()
        record["namespace"] = "rate_limits"
        self.assertInvalid(record, "checkpoint")

    def test_checkpoint_key_and_time(self):
        for key in ('["2504.17524"]', '[ "2504.17523" ]', "[]", "2504.17523"):
            with self.subTest(key=key):
                record = date_checkpoint(); record["key"] = key
                self.assertInvalid(record, "checkpoint")
        record = date_checkpoint(); record["updated_at"] = "2026-10-27T00:00:00"
        self.assertInvalid(record, "checkpoint")

    def test_discovery_batch_must_partition_candidates(self):
        cases = [("found_ids", ["2504.00001", "2504.00003"]),  # one ID in two groups
                 ("unresolved_ids", []),                         # an ID disappears
                 ("error", None),                                # unresolved without error
                 ("offset", 3)]                                  # key no longer matches
        for field, value in cases:
            with self.subTest(field=field):
                record = discovery_checkpoint(); record["payload"][field] = value
                self.assertInvalid(record, "checkpoint")
        record = discovery_checkpoint()
        record["payload"].update(found_ids=["2504.00001", "2504.00003"], unresolved_ids=[], error=None)
        self.assertValid(record, "checkpoint")

    def test_reference_pages_are_contiguous(self):
        record = references_checkpoint(); record["payload"]["pages"][1]["offset"] = 3
        self.assertInvalid(record, "checkpoint")
        record = references_checkpoint(); record["payload"]["pages"][0]["next_offset"] = None
        self.assertInvalid(record, "checkpoint")
        record = references_checkpoint(); record["payload"]["pages"][0]["raw_items"].append({})
        self.assertInvalid(record, "checkpoint")
        record = references_checkpoint(); record["key"] = canonical(["trong", "2504.17523"])
        self.assertInvalid(record, "checkpoint")

    def test_date_and_rate_payload_rules(self):
        record = date_checkpoint(); record["payload"]["submission_date"] = "24/04/2025"
        self.assertInvalid(record, "checkpoint")
        record = rate_checkpoint(); record["payload"]["recent_requests_at"].reverse()
        self.assertInvalid(record, "checkpoint")
        record = rate_checkpoint(); record["payload"]["consecutive_429"] = -1
        self.assertInvalid(record, "checkpoint")

    def test_snapshot_rules(self):
        cases = [("files", []), ("owners", ["huynh-nhi", "huynh-nhi"]), ("exporter_commit", "abc"),
                 ("config_fingerprint", "C" * 64), ("created_at", "2026-10-27"), ("snapshot_id", "a b"),
                 ("schema_version", "1.1")]
        for field, value in cases:
            with self.subTest(field=field):
                bad = snapshot(); bad[field] = value
                self.assertInvalid(bad, "snapshot")
        for path in ("../secret", "/etc/passwd", "C:/x", "raw\\a.json", "raw//a.json"):
            with self.subTest(path=path):
                bad = snapshot(); bad["files"][0]["path"] = path
                self.assertInvalid(bad, "snapshot")
        bad = snapshot(); bad["files"].append(deepcopy(bad["files"][0]))
        self.assertInvalid(bad, "snapshot")

    def test_collection_keeps_one_handoff_per_owner(self):
        bad = collection(); bad["members"].append(member("nhi-2", ["huynh-nhi"]))
        self.assertInvalid(bad, "collection")
        bad = collection(); bad["members"][1]["snapshot_id"] = "nhi"
        self.assertInvalid(bad, "collection")
        bad = collection(); bad["members"] = []
        self.assertInvalid(bad, "collection")

    def test_statistics_add_up_from_counts(self):
        bad = statistics(); bad["summary"]["papers_complete"] = 3
        self.assertInvalid(bad, "collection-statistics")
        bad = statistics(); bad["missing_owners"] = ["huynh-nhi"]
        self.assertInvalid(bad, "collection-statistics")

    def test_statistics_rates_follow_schema_formulas(self):
        cases = [("paper_success_rate", 0.75),           # not papers_complete / papers_discovered
                 ("metadata_success_rate", None),        # null although the denominator is 4
                 ("mean_retained_source_bytes", None)]   # samples exist, so the mean is known
        for field, value in cases:
            with self.subTest(field=field):
                bad = statistics(); bad["by_owner"]["huynh-nhi"][field] = value
                self.assertInvalid(bad, "collection-statistics")
        bad = statistics()
        for s in (bad["summary"], bad["by_owner"]["huynh-nhi"]):
            s["source_versions_unresolved"] = 0  # versions no longer add up to expected
        self.assertInvalid(bad, "collection-statistics")

    def test_statistics_scope_and_owners_match_paper_tasks(self):
        bad = statistics(); bad["scope"]["paper_count"] = 3
        self.assertInvalid(bad, "collection-statistics")
        bad = statistics(); bad["scope"]["paper_tasks"].append(dict(NHI_PAPERS[0]))
        self.assertInvalid(bad, "collection-statistics")
        bad = statistics(); bad["by_owner"]["trong"] = summary()  # owner without papers
        self.assertInvalid(bad, "collection-statistics")
        empty = statistics()
        empty["scope"].update(paper_tasks=[], owner_count=0, paper_count=0)
        empty.update(summary=summary(), by_owner={})
        self.assertValid(empty, "collection-statistics")

    def test_documents_are_not_modified(self):
        for document, kind in ((discovery_checkpoint(), "checkpoint"), (snapshot(), "snapshot")):
            before = json.dumps(document, sort_keys=True)
            validate_pipeline_document(document, kind)
            self.assertEqual(json.dumps(document, sort_keys=True), before)


if __name__ == "__main__":
    unittest.main()
