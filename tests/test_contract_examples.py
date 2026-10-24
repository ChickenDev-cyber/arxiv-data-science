"""Check that shared fixtures can be consumed according to the team schema."""

import json
import tempfile
import unittest
from pathlib import Path

from arxiv_pipeline.examples import generate
from arxiv_pipeline.contracts import SCHEMA_VERSION


def resolve_pointer(document, pointer):
    current = document
    for token in pointer.split("/")[1:]:
        token = token.replace("~1", "/").replace("~0", "~")
        current = current[int(token)] if isinstance(current, list) else current[token]
    return current


class ContractExamplesTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="arxiv-contracts-test-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.events = generate(self.root)

    def test_version_and_offline_request_count(self):
        self.assertEqual(SCHEMA_VERSION, "1.2")
        for event in self.events:
            self.assertEqual(event["schema_version"], SCHEMA_VERSION)
            self.assertTrue(event["is_mock"])
            self.assertEqual(event["attempts"], 0)

    def test_missing_metadata_uses_json_pointers_and_incomplete_history(self):
        cases = json.loads((self.root / "examples/edge_cases.json").read_text())
        partial = next(c for c in cases if c["step"] == "metadata" and c["status"] == "partial")
        self.assertFalse(partial["data"]["versions_complete"])
        self.assertEqual(set(partial["missing_fields"]),
                         {"/data/submission_date", "/data/versions/0/submitted_at"})
        for pointer in partial["missing_fields"]:
            self.assertIsNone(resolve_pointer(partial, pointer))

    def test_reference_handoff_preserves_raw_items(self):
        accepted = [json.loads(line) for line in
                    (self.root / "runs/mock-demo/accepted_results.jsonl").read_text().splitlines()]
        references = [event for event in accepted if event["step"] == "references"]
        self.assertEqual(len(references), 5)
        for event in references:
            with self.subTest(owner=event["owner"]):
                data = event["data"]
                self.assertIsInstance(data.get("raw_references"), list)
                expected_count = 1 if event["owner"] == "hoang" else 2
                self.assertEqual(len(data["raw_references"]), expected_count)
                self.assertEqual(event["metrics"]["s2_reference_count"], expected_count)
                self.assertEqual(len(data["references"]), 0 if event["owner"] == "hoang" else 1)
                self.assertEqual(data["raw_references"][-1]["citedPaper"]["externalIds"],
                                 {"DOI": "10.0000/synthetic"})
                sample = json.loads((self.root / "examples" / event["owner"] /
                                     "references.result.json").read_text())
                self.assertEqual(sample, event)
                metadata = next(e for e in accepted if e["step"] == "metadata" and e["owner"] == event["owner"])
                corpus = (self.root / "raw" / event["owner"] /
                          metadata["arxiv_id"].replace(".", "-"))
                self.assertEqual(json.loads((corpus / "raw_references.json").read_text()), data["raw_references"])

    def test_sources_have_staging_and_consistent_byte_metrics(self):
        required = {"download_bytes", "extracted_bytes_before_filter", "retained_source_bytes",
                    "removed_figure_bytes", "removed_other_bytes"}
        for event in self.events:
            if event["step"] != "sources":
                continue
            self.assertEqual(set(event["metrics"]), required)
            stage = (self.root / "staging" / event["run_id"] / event["owner"] /
                     event["data"]["source_dir"].split("/")[-2] / f"v{event['version']}")
            self.assertTrue(stage.is_dir())
            retained = 0
            for item in event["data"]["files"]:
                payload = (stage / item["path"]).read_bytes()
                self.assertEqual(len(payload), item["size_bytes"])
                self.assertEqual(payload, (self.root / event["data"]["source_dir"] / item["path"]).read_bytes())
                retained += len(payload)
            self.assertEqual(retained, event["metrics"]["retained_source_bytes"])
            if event["status"] == "success":
                self.assertTrue(all(value is not None for value in event["metrics"].values()))
                self.assertEqual(event["metrics"]["extracted_bytes_before_filter"],
                                 retained + event["metrics"]["removed_figure_bytes"] +
                                 event["metrics"]["removed_other_bytes"])

    def test_exported_scope_and_accepted_results_are_consistent(self):
        base = self.root / "runs/mock-demo"
        wrapper = json.loads((base / "scope.json").read_text())
        scope = wrapper["scope"]
        accepted = [json.loads(line) for line in (base / "accepted_results.jsonl").read_text().splitlines()]
        self.assertEqual(wrapper["schema_version"], SCHEMA_VERSION)
        self.assertFalse(scope["discovery_complete"])
        self.assertTrue(scope["versions_complete"])
        self.assertEqual(scope["owner_count"], 5)
        self.assertEqual(scope["paper_count"], len(scope["paper_tasks"]))
        tasks = {(task["owner"], task["arxiv_id"]) for task in scope["paper_tasks"]}
        keys = set()
        for event in accepted:
            key = (event["owner"], event["step"], event["arxiv_id"], event["version"])
            self.assertNotIn(key, keys)
            keys.add(key)
            self.assertEqual(event["run_id"], wrapper["run_id"])
            self.assertEqual(event["is_mock"], wrapper["is_mock"])
            if event["step"] != "discovery":
                self.assertIn((event["owner"], event["arxiv_id"]), tasks)

    def test_serialized_outputs_match_returned_events(self):
        events = [json.loads(line) for line in (self.root / "logs/mock-demo/events.jsonl").read_text().splitlines()]
        self.assertEqual(events, self.events)
        for event in self.events:
            if event["step"] == "sources":
                manifest = self.root / event["data"]["source_dir"] / "source_manifest.json"
                self.assertEqual(json.loads(manifest.read_text()), event)


if __name__ == "__main__":
    unittest.main()
