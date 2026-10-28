from contextlib import contextmanager, redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from arxiv_pipeline.collection import (CollectionAssembler, CollectionDirectoryReader,
                                       CollectionStatisticsBuilder, combine_summaries)
from arxiv_pipeline.collection.cli import main
from arxiv_pipeline.config import ASSIGNED_RANGES
from arxiv_pipeline.pipeline_contracts import VerifiedRun

FINGERPRINT = "f" * 64
ORIGIN = "/content/out"


def inventory(corpus):
    """Size and hash of every file under a corpus folder, like a run's exact inventory."""
    files = []
    for path in sorted(p for p in Path(corpus).rglob("*") if p.is_file()):
        data = path.read_bytes()
        files.append({"path": path.relative_to(corpus).as_posix(), "size_bytes": len(data),
                      "sha256": hashlib.sha256(data).hexdigest()})
    return files


def write_snapshot(root, folder, snapshot_id, owners, created_at="2026-10-27T00:00:00Z", is_mock=True):
    """An unpacked snapshot in the agreed layout: <folder>/manifest.json next to <folder>/corpus/."""
    base = Path(root) / folder
    (base / "corpus/raw").mkdir(parents=True)
    (base / "corpus/raw/metadata.json").write_text(json.dumps({"snapshot": snapshot_id}))
    manifest = {"format_version": 1, "kind": "arxiv-snapshot", "snapshot_id": snapshot_id,
                "schema_version": "1.2", "run_id": snapshot_id + "-run", "is_mock": is_mock,
                "owners": owners, "created_at": created_at, "exporter_commit": "a" * 40,
                "producer_commits": [], "unknown_provenance_tasks": 0, "config_fingerprint": FINGERPRINT,
                "origin_output_root": ORIGIN, "files": inventory(base / "corpus")}
    (base / "manifest.json").write_text(json.dumps(manifest))
    return manifest


def verified_run(root, run_id, owners, papers=(), discovery_complete=False, origin=ORIGIN):
    ranges = [dict(c) for c in ASSIGNED_RANGES if c["owner"] in owners]
    tasks = [{"owner": o, "arxiv_id": a} for o, a in papers]
    scope = {"schema_version": "1.2", "run_id": run_id, "is_mock": True,
             "scope": {"ranges": ranges, "paper_tasks": tasks, "discovery_complete": discovery_complete,
                       "versions_complete": True, "owner_count": len({o for o, _ in papers}),
                       "paper_count": len(tasks)}}
    return VerifiedRun(output_root=Path(root), scope=scope, accepted_results=[],
                       validation={"valid": True, "issues": []}, quality={"valid": True, "issues": []},
                       files=inventory(root), run_state="finished", config_fingerprint=FINGERPRINT,
                       recorded_output_root=origin)


class FakeRunReader:
    """Stands in for RunInspector: returns a VerifiedRun built from the corpus it is asked for."""

    def __init__(self, owners_by_run, papers_by_run=None, **options):
        self.owners_by_run, self.papers_by_run, self.options = owners_by_run, papers_by_run or {}, options
        self.opened, self.closed = [], []

    @contextmanager
    def open(self, output_root, run_id):
        self.opened.append(run_id)
        try:
            yield verified_run(output_root, run_id, self.owners_by_run[run_id],
                               self.papers_by_run.get(run_id, ()), **self.options)
        finally:
            self.closed.append(run_id)


def summary(discovered, complete, expected=0, success=0, samples=0, mean=None):
    """Counts for one owner; rates are derived from them as schema spec section 11 defines."""
    counts = {"papers_discovered": discovered, "metadata_complete_papers": complete, "papers_complete": complete,
              "source_versions_expected": expected, "source_versions_success": success,
              "source_versions_unsupported": 0, "source_versions_unresolved": expected - success,
              "references_fully_fetched_papers": complete, "references_metadata_complete_papers": 0,
              "references_arxiv_total": 0, "references_complete_total": 0, "source_size_sample_count": samples,
              "mean_extracted_bytes_before_filter": mean, "mean_retained_source_bytes": mean}
    return combine_summaries([counts])


class FakeSummarizer:
    def __init__(self, values):
        self.values = values

    def summarize(self, run, owner):
        return self.values[owner]


class AssemblerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.assembler = CollectionAssembler(clock=lambda: "2026-10-28T00:00:00Z")

    def tearDown(self):
        self.tmp.cleanup()

    def test_one_member_per_owner_newest_wins(self):
        write_snapshot(self.root, "s/nhi-old", "nhi-old", ["huynh-nhi"], "2026-10-26T00:00:00Z")
        write_snapshot(self.root, "s/nhi-new", "nhi-new", ["huynh-nhi"], "2026-10-27T00:00:00Z")
        write_snapshot(self.root, "s/trong", "trong", ["trong", "khang"])
        result = self.assembler.assemble(self.root, "group-1", ["s/nhi-old", "s/nhi-new", "s/trong"])
        members = {m["snapshot_id"]: m for m in result.manifest["members"]}
        self.assertEqual(sorted(members), ["nhi-new", "trong"])
        self.assertEqual(result.skipped, ["nhi-old"])
        self.assertEqual(members["nhi-new"]["root"], "s/nhi-new/corpus")
        self.assertEqual(members["nhi-new"]["manifest_path"], "s/nhi-new/manifest.json")
        self.assertEqual(result.manifest["created_at"], "2026-10-28T00:00:00Z")

    def test_snapshot_layout_manifest_next_to_corpus(self):
        write_snapshot(self.root, "s/nhi", "nhi", ["huynh-nhi"])
        member = self.assembler.assemble(self.root, "g", ["s/nhi/"]).manifest["members"][0]
        self.assertEqual((member["root"], member["manifest_path"]), ("s/nhi/corpus", "s/nhi/manifest.json"))
        # Old layout: files beside the manifest instead of inside corpus/.
        old = self.root / "old"
        (self.root / "s/nhi/corpus").rename(old)
        (old / "manifest.json").write_bytes((self.root / "s/nhi/manifest.json").read_bytes())
        with self.assertRaisesRegex(ValueError, "missing or changed"):
            self.assembler.assemble(self.root, "g", ["s/nhi"])
        with self.assertRaisesRegex(ValueError, "missing or changed"):
            self.assembler.assemble(self.root, "g", ["old"])

    def test_rejects_changed_files_duplicates_and_mixed_modes(self):
        write_snapshot(self.root, "s/a", "a", ["trong"])
        (self.root / "s/a/corpus/raw/metadata.json").write_text("changed")
        with self.assertRaisesRegex(ValueError, "missing or changed"):
            self.assembler.assemble(self.root, "g", ["s/a"])
        write_snapshot(self.root, "s/b", "b", ["trong"])
        with self.assertRaisesRegex(ValueError, "twice"):
            self.assembler.assemble(self.root, "g", ["s/b", "s/b"])
        write_snapshot(self.root, "s/c", "c", ["khang"], is_mock=False)
        with self.assertRaisesRegex(ValueError, "mix"):
            self.assembler.assemble(self.root, "g", ["s/b", "s/c"])
        with self.assertRaises(ValueError):
            self.assembler.assemble(self.root, "g", [])
        with self.assertRaises(ValueError):
            self.assembler.assemble(self.root, "g", ["../outside"])

    def test_save_writes_valid_manifest(self):
        write_snapshot(self.root, "s/a", "a", ["trong"])
        manifest = self.assembler.assemble(self.root, "g", ["s/a"]).manifest
        path = CollectionAssembler.save(self.root, manifest)
        self.assertEqual(json.loads(path.read_text()), manifest)
        with self.assertRaises(ValueError):
            CollectionAssembler.save(self.root, {**manifest, "members": []})


class AssembleCliTests(unittest.TestCase):
    def test_assemble_command_writes_manifest_and_reports_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_snapshot(root, "s/old", "old", ["huynh-nhi"], "2026-10-26T00:00:00Z")
            write_snapshot(root, "s/new", "new", ["huynh-nhi"], "2026-10-27T00:00:00Z")
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(["assemble", "--root", tmp, "--collection-id", "demo", "s/old", "s/new"])
            report = json.loads(out.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(report["members"], [{"snapshot_id": "new", "owners": ["huynh-nhi"]}])
            self.assertEqual(report["skipped"], ["old"])
            self.assertTrue((root / "collection.json").is_file())
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                self.assertEqual(main(["assemble", "--root", tmp, "--collection-id", "demo", "s/missing"]), 1)
            self.assertIn("collection:", err.getvalue())


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_snapshot(self.root, "s/nhi", "nhi", ["huynh-nhi"])
        write_snapshot(self.root, "s/trong", "trong", ["khang", "trong"])
        assembler = CollectionAssembler(clock=lambda: "2026-10-28T00:00:00Z")
        CollectionAssembler.save(self.root, assembler.assemble(self.root, "g", ["s/nhi", "s/trong"]).manifest)
        self.runs = FakeRunReader({"nhi-run": ["huynh-nhi"], "trong-run": ["khang", "trong"]})

    def tearDown(self):
        self.tmp.cleanup()

    def test_opens_every_member_and_closes_them(self):
        with CollectionDirectoryReader(self.runs).open(self.root) as collection:
            self.assertEqual([r.scope["run_id"] for r in collection.runs], ["nhi-run", "trong-run"])
            self.assertEqual(self.runs.closed, [])
            self.assertEqual(collection.runs[0].output_root, self.root / "s/nhi/corpus")
        self.assertEqual(sorted(self.runs.closed), ["nhi-run", "trong-run"])

    def test_rejects_tampered_snapshot_or_mismatched_run(self):
        (self.root / "s/trong/corpus/raw/metadata.json").write_text("changed")
        with self.assertRaisesRegex(ValueError, "missing or changed"):
            with CollectionDirectoryReader(self.runs).open(self.root):
                pass
        self.assertEqual(self.runs.opened, self.runs.closed)  # nothing left open after a failure
        write_snapshot(self.root, "t/nhi", "nhi", ["huynh-nhi"])
        wrong = FakeRunReader({"nhi-run": ["trong"]})
        assembler = CollectionAssembler()
        CollectionAssembler.save(self.root, assembler.assemble(self.root, "g", ["t/nhi"]).manifest)
        with self.assertRaisesRegex(ValueError, "differs from its verified run: owners"):
            with CollectionDirectoryReader(wrong).open(self.root):
                pass

    def test_rejects_run_from_another_origin(self):
        moved = FakeRunReader({"nhi-run": ["huynh-nhi"], "trong-run": ["khang", "trong"]}, origin="/other/out")
        with self.assertRaisesRegex(ValueError, "origin_output_root"):
            with CollectionDirectoryReader(moved).open(self.root):
                pass

    def test_manifest_must_list_the_exact_run_inventory(self):
        # A file the run inventory covers but the manifest leaves out (e.g. state.sqlite).
        (self.root / "s/nhi/corpus/state.sqlite").write_bytes(b"db")
        with self.assertRaisesRegex(ValueError, "differs from its verified run: files"):
            with CollectionDirectoryReader(self.runs).open(self.root):
                pass

    def test_rejects_missing_or_invalid_collection_manifest(self):
        (self.root / "collection.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "Invalid collection"):
            with CollectionDirectoryReader(self.runs).open(self.root):
                pass
        (self.root / "collection.json").unlink()
        with self.assertRaisesRegex(ValueError, "Cannot read"):
            with CollectionDirectoryReader(self.runs).open(self.root):
                pass


class StatisticsTests(unittest.TestCase):
    def test_rates_come_from_summed_counts(self):
        total = combine_summaries([summary(10, 9, 4, 4, 2, 100.0), summary(30, 3, 6, 0, 6, 200.0)])
        self.assertEqual(total["papers_discovered"], 40)
        self.assertEqual(total["paper_success_rate"], 12 / 40)  # not (0.9 + 0.1) / 2
        self.assertEqual(total["source_success_rate"], 4 / 10)
        self.assertEqual(total["mean_retained_source_bytes"], (2 * 100 + 6 * 200) / 8)
        self.assertIsNone(total["reference_metadata_success_rate"])  # 0 / 0 is unknown, not 0

    def test_builds_valid_statistics_with_missing_owners(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_snapshot(root, "s/nhi", "nhi", ["huynh-nhi"])
            write_snapshot(root, "s/trong", "trong", ["khang", "trong"])
            CollectionAssembler.save(root, CollectionAssembler().assemble(root, "g", ["s/nhi", "s/trong"]).manifest)
            runs = FakeRunReader({"nhi-run": ["huynh-nhi"], "trong-run": ["khang", "trong"]},
                                 {"nhi-run": [("huynh-nhi", f"2409.{n:05d}") for n in range(3594, 3598)],
                                  "trong-run": [("trong", f"2504.{n:05d}") for n in range(17523, 17529)]})
            # Khang is in the trong snapshot but has no selected paper, so gets no summary.
            values = {"huynh-nhi": summary(4, 2), "trong": summary(6, 6)}
            with CollectionDirectoryReader(runs).open(root) as collection:
                stats = CollectionStatisticsBuilder(FakeSummarizer(values)).build(collection)
        self.assertEqual(stats["missing_owners"], ["hoang", "man-nhi"])
        self.assertEqual(sorted(stats["by_owner"]), ["huynh-nhi", "trong"])
        self.assertEqual(stats["summary"]["papers_complete"], 8)
        self.assertEqual(stats["summary"]["paper_success_rate"], 8 / 10)
        self.assertEqual(stats["scope"]["paper_count"], 10)

    def test_matches_worked_example_of_schema_spec(self):
        # Section 15 of the schema spec: rates recomputed from these counts must match.
        example = {"papers_discovered": 2, "metadata_complete_papers": 1, "papers_complete": 0,
                   "paper_success_rate": 0.0, "source_versions_expected": 2, "source_versions_success": 1,
                   "source_versions_unsupported": 1, "source_versions_unresolved": 0,
                   "references_fully_fetched_papers": 1, "references_query_success_rate": 0.5,
                   "references_metadata_complete_papers": 0, "metadata_success_rate": 0.5,
                   "source_success_rate": 0.5, "references_arxiv_total": 3, "references_complete_total": 2,
                   "reference_metadata_success_rate": 2 / 3, "mean_arxiv_references_per_paper": 3.0,
                   "source_size_sample_count": 1, "mean_extracted_bytes_before_filter": 1000.0,
                   "mean_retained_source_bytes": 400.0}
        self.assertEqual(combine_summaries([example]), example)
        self.assertEqual(combine_summaries([example, example])["mean_arxiv_references_per_paper"], 3.0)

    def test_discovery_complete_needs_all_five_ranges(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_snapshot(root, "s/nhi", "nhi", ["huynh-nhi"])
            write_snapshot(root, "s/rest", "rest", ["khang", "trong", "hoang", "man-nhi"])
            for members in (["s/nhi"], ["s/nhi", "s/rest"]):
                CollectionAssembler.save(root, CollectionAssembler().assemble(root, "g", members).manifest)
                runs = FakeRunReader({"nhi-run": ["huynh-nhi"], "rest-run": ["khang", "trong", "hoang", "man-nhi"]},
                                     discovery_complete=True)
                with CollectionDirectoryReader(runs).open(root) as collection:
                    stats = CollectionStatisticsBuilder(FakeSummarizer({})).build(collection)
                with self.subTest(members=members):
                    # Huỳnh Nhi's run alone finished discovery, but four ranges are still missing.
                    self.assertEqual(stats["scope"]["discovery_complete"], len(members) == 2)

    def test_missing_mean_for_sampled_owner_is_an_error(self):
        with self.assertRaises(ValueError):
            combine_summaries([summary(1, 1, samples=3, mean=None)])


if __name__ == "__main__":
    unittest.main()
