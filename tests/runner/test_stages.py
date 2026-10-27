from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from arxiv_pipeline.runner import service
from arxiv_pipeline.runner.mock import make_mock_modules
from arxiv_pipeline.runner.models import ModuleSet, RunOptions
from arxiv_pipeline.runner.state import StateStore


def recording_modules(options, calls):
    modules = make_mock_modules(options)

    def metadata(task, context):
        calls.append(("metadata", task["arxiv_id"]))
        return modules.metadata.fetch_metadata(task, context)

    def sources(task, version, context):
        calls.append(("sources", task["arxiv_id"], version))
        return modules.sources.fetch_sources(task, version, context)

    def references(task, context):
        calls.append(("references", task["arxiv_id"]))
        return modules.references.fetch_references(task, context)

    return ModuleSet(modules.discovery, SimpleNamespace(fetch_metadata=metadata),
                     SimpleNamespace(fetch_sources=sources), SimpleNamespace(fetch_references=references))


class StagesTests(unittest.TestCase):
    def run_stage(self, options, modules, stage="all", **kwargs):
        self.assertTrue(hasattr(service, "PipelineRunner"), "OOP stage runner is missing")

        class Factory:
            def build(self, options, checkpoints):
                return modules

        return service.PipelineRunner(Factory()).run(options, stage=stage, **kwargs)

    def test_sources_never_calls_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            options = RunOptions("source-stage", Path(tmp), ("trong",), True)
            calls = []
            result = self.run_stage(options, recording_modules(options, calls), "sources")
            self.assertEqual([call[0] for call in calls], ["metadata", "sources", "sources"])
            self.assertEqual(result.exit_code, 2)
            self.assertTrue(result.validation["valid"])
            self.assertFalse(result.quality["valid"])
            self.assertTrue((Path(tmp) / "raw/trong/2504-17523/v2/main.tex").is_file())
            self.assertFalse((Path(tmp) / "raw/trong/2504-17523/references.json").exists())

    def test_references_does_not_require_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            options = RunOptions("references-stage", Path(tmp), ("trong",), True)
            calls = []
            result = self.run_stage(options, recording_modules(options, calls), "references")
            self.assertEqual(calls, [("references", "2504.17523")])
            self.assertTrue(result.validation["valid"])
            self.assertFalse(result.scope["scope"]["versions_complete"])
            self.assertTrue((Path(tmp) / "raw/trong/2504-17523/references.json").is_file())
            self.assertFalse((Path(tmp) / "raw/trong/2504-17523/metadata.json").exists())

    def test_all_runs_sources_for_every_paper_before_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            options = RunOptions("all-stages", Path(tmp), ("trong", "hoang"), True)
            calls = []
            result = self.run_stage(options, recording_modules(options, calls))
            first_reference = next(i for i, call in enumerate(calls) if call[0] == "references")
            self.assertEqual(len([c for c in calls[:first_reference] if c[0] == "sources"]), 4)
            self.assertTrue(all(c[0] == "references" for c in calls[first_reference:]))
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(result.scope["scope"]["paper_count"], 2)

    def test_resume_keeps_good_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            options = RunOptions("changing-stage", Path(tmp), ("trong",), True)
            calls = []
            modules = recording_modules(options, calls)
            first = self.run_stage(options, modules, "sources")
            before_scope = first.scope
            calls.clear()
            second = self.run_stage(options, modules, "references")
            self.assertEqual(calls, [("references", "2504.17523")])
            self.assertEqual(second.scope, before_scope)
            self.assertEqual(second.exit_code, 0)
            calls.clear()
            final = self.run_stage(options, modules, "all")
            self.assertEqual(calls, [])
            self.assertEqual(final.executed_tasks, 0)
            self.assertEqual(final.reused_tasks, 5)

    def test_discovery_stage_only_and_invalid_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "run"
            options = RunOptions("discovery-stage", root, ("trong",), True)
            calls = []
            result = self.run_stage(options, recording_modules(options, calls), "discovery")
            self.assertEqual(calls, [])
            self.assertEqual(result.executed_tasks, 1)
            self.assertEqual(result.exit_code, 2)
            other = replace(options, output_root=Path(tmp) / "invalid")
            with self.assertRaises(ValueError):
                self.run_stage(other, make_mock_modules(other), "invalid")
            self.assertFalse(other.output_root.exists())

    def test_legacy_run_pipeline_wrapper_still_works(self):
        self.assertTrue(hasattr(service, "PipelineRunner"), "OOP stage runner is missing")
        with tempfile.TemporaryDirectory() as tmp:
            options = RunOptions("legacy-wrapper", Path(tmp), ("trong",), True)
            self.assertEqual(service.run_pipeline(options, make_mock_modules(options)).exit_code, 0)

    def test_producer_commit_only_labels_new_results(self):
        self.assertTrue(hasattr(service, "PipelineRunner"), "OOP stage runner is missing")
        with tempfile.TemporaryDirectory() as tmp:
            options = RunOptions("provenance", Path(tmp), ("trong",), True)
            self.run_stage(options, make_mock_modules(options), "sources")
            before = (Path(tmp) / "raw/trong/2504-17523/v1/source_manifest.json").read_bytes()
            modules = make_mock_modules(options)

            class Factory:
                def build(self, options, checkpoints):
                    return modules

            service.PipelineRunner(Factory(), producer_commit="a" * 40).run(options, stage="references")
            self.assertEqual((Path(tmp) / "raw/trong/2504-17523/v1/source_manifest.json").read_bytes(), before)
            with StateStore.readonly(Path(tmp) / "state.sqlite", options.run_id) as store:
                results = list(store.iter_results(accepted_only=True))
            self.assertTrue(all("producer_commit" not in r["provenance"] for r in results if r["step"] != "references"))
            self.assertEqual(next(r for r in results if r["step"] == "references")["provenance"]["producer_commit"], "a" * 40)
