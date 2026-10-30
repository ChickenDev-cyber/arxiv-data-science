from contextlib import contextmanager, redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from arxiv_pipeline.collection import (CollectionAssembler, CollectionDirectoryReader, CollectionService,
                                       CollectionStatisticsBuilder, combine_summaries)
from arxiv_pipeline.collection.cli import main
from arxiv_pipeline.config import ASSIGNED_RANGES
from arxiv_pipeline.pipeline_contracts import VerifiedRun

FINGERPRINT = "f" * 64
ORIGIN = "/content/out"
COMMIT = "b" * 40


def inventory(corpus):
    """Size and hash of every file under a corpus folder, like a run's exact inventory."""
    files = []
    for path in sorted(p for p in Path(corpus).rglob("*") if p.is_file()):
        data = path.read_bytes()
        files.append({"path": path.relative_to(corpus).as_posix(), "size_bytes": len(data),
                      "sha256": hashlib.sha256(data).hexdigest()})
    return files


def write_snapshot(root, folder, snapshot_id, owners, created_at="2026-10-27T00:00:00Z", is_mock=True,
                   producer_commits=(), unknown_provenance_tasks=0):
    """An unpacked snapshot in the agreed layout: <folder>/manifest.json next to <folder>/corpus/."""
    base = Path(root) / folder
    (base / "corpus/raw").mkdir(parents=True)
    (base / "corpus/raw/metadata.json").write_text(json.dumps({"snapshot": snapshot_id}))
    manifest = {"format_version": 1, "kind": "arxiv-snapshot", "snapshot_id": snapshot_id,
                "schema_version": "1.2", "run_id": snapshot_id + "-run", "is_mock": is_mock,
                "owners": owners, "created_at": created_at, "exporter_commit": "a" * 40,
                "producer_commits": list(producer_commits), "unknown_provenance_tasks": unknown_provenance_tasks,
                "config_fingerprint": FINGERPRINT,
                "origin_output_root": ORIGIN, "files": inventory(base / "corpus")}
    (base / "manifest.json").write_text(json.dumps(manifest))
    return manifest


def metadata_result(owner, arxiv_id, versions_complete=True, commit=COMMIT):
    """The fields of an accepted metadata Result that the collection code reads."""
    return {"owner": owner, "arxiv_id": arxiv_id, "step": "metadata",
            "data": {"versions_complete": versions_complete},
            "provenance": {"producer_commit": commit} if commit else {}}


def verified_run(root, run_id, owners, papers=(), discovery_complete=False, origin=ORIGIN, accepted=None):
    ranges = [dict(c) for c in ASSIGNED_RANGES if c["owner"] in owners]
    tasks = [{"owner": o, "arxiv_id": a} for o, a in papers]
    scope = {"schema_version": "1.2", "run_id": run_id, "is_mock": True,
             "scope": {"ranges": ranges, "paper_tasks": tasks, "discovery_complete": discovery_complete,
                       "versions_complete": False, "owner_count": len({o for o, _ in papers}),
                       "paper_count": len(tasks)}}
    return VerifiedRun(output_root=Path(root), scope=scope, accepted_results=list((accepted or {}).get(run_id, ())),
                       validation={"valid": True, "issues": []}, quality={"valid": True, "issues": []},
                       files=inventory(root), run_state="finished", config_fingerprint=FINGERPRINT,
                       recorded_output_root=origin)


class FakeRunReader:
    """Stands in for RunInspector: returns a VerifiedRun built from the corpus it is asked for."""

    def __init__(self, owners_by_run, papers_by_run=None, accepted=None, **options):
        self.owners_by_run, self.papers_by_run = owners_by_run, papers_by_run or {}
        self.options = {**options, "accepted": accepted or {}}
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
              "references_fully_fetched_papers": complete, "references_metadata_complete_papers": complete,
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

    def test_one_member_per_snapshot(self):
        write_snapshot(self.root, "s/nhi", "nhi", ["huynh-nhi"])
        write_snapshot(self.root, "s/trong", "trong", ["trong", "khang"])
        manifest = self.assembler.assemble(self.root, "group-1", ["s/trong", "s/nhi"])
        members = {m["snapshot_id"]: m for m in manifest["members"]}
        self.assertEqual(sorted(members), ["nhi", "trong"])
        self.assertEqual(members["nhi"]["root"], "s/nhi/corpus")
        self.assertEqual(members["nhi"]["manifest_path"], "s/nhi/manifest.json")
        self.assertEqual(manifest["created_at"], "2026-10-28T00:00:00Z")

    def test_two_snapshots_of_one_owner_are_an_error(self):
        write_snapshot(self.root, "s/nhi-old", "nhi-old", ["huynh-nhi"], "2026-10-26T00:00:00Z")
        write_snapshot(self.root, "s/nhi-new", "nhi-new", ["huynh-nhi"], "2026-10-27T00:00:00Z")
        write_snapshot(self.root, "s/both", "both", ["trong", "huynh-nhi"])
        for folders in (["s/nhi-old", "s/nhi-new"], ["s/nhi-new", "s/both"]):
            with self.subTest(folders=folders), self.assertRaisesRegex(ValueError, "owner.*huynh-nhi"):
                self.assembler.assemble(self.root, "g", folders)

    def test_snapshot_layout_manifest_next_to_corpus(self):
        write_snapshot(self.root, "s/nhi", "nhi", ["huynh-nhi"])
        member = self.assembler.assemble(self.root, "g", ["s/nhi/"])["members"][0]
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

    def test_save_writes_valid_manifest_once(self):
        write_snapshot(self.root, "s/a", "a", ["trong"])
        manifest = self.assembler.assemble(self.root, "g", ["s/a"])
        with self.assertRaises(ValueError):
            CollectionAssembler.save(self.root, {**manifest, "members": []})
        path = CollectionAssembler.save(self.root, manifest)
        self.assertEqual(json.loads(path.read_text()), manifest)
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "already exists"):
            CollectionAssembler.save(self.root, {**manifest, "collection_id": "other"})
        self.assertEqual(path.read_bytes(), before)  # the existing collection is untouched
        self.assertEqual([p.name for p in self.root.iterdir() if p.name.startswith(".collection-")], [])


class AssembleCliTests(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(argv))
        return code, (json.loads(out.getvalue()) if out.getvalue() else None), err.getvalue()

    def test_assemble_command_writes_manifest_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_snapshot(root, "s/nhi", "nhi", ["huynh-nhi"])
            write_snapshot(root, "s/nhi-2", "nhi-2", ["huynh-nhi"])
            code, report, _ = self.run_cli("assemble", "--root", tmp, "--collection-id", "demo", "s/nhi")
            self.assertEqual(code, 0)
            self.assertEqual(report["members"], [{"snapshot_id": "nhi", "owners": ["huynh-nhi"]}])
            self.assertTrue((root / "collection.json").is_file())
            code, report, err = self.run_cli("assemble", "--root", tmp, "--collection-id", "demo", "s/nhi")
            self.assertEqual((code, report), (1, None))
            self.assertIn("already exists", err)
            (root / "collection.json").unlink()
            for folders in (["s/missing"], ["s/nhi", "s/nhi-2"]):
                with self.subTest(folders=folders):
                    code, report, err = self.run_cli("assemble", "--root", tmp, "--collection-id", "demo", *folders)
                    self.assertEqual((code, report), (1, None))
                    self.assertIn("collection:", err)
            self.assertFalse((root / "collection.json").exists())


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_snapshot(self.root, "s/nhi", "nhi", ["huynh-nhi"])
        write_snapshot(self.root, "s/trong", "trong", ["khang", "trong"])
        assembler = CollectionAssembler(clock=lambda: "2026-10-28T00:00:00Z")
        CollectionAssembler.save(self.root, assembler.assemble(self.root, "g", ["s/nhi", "s/trong"]))
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
        (self.root / "collection.json").unlink()
        CollectionAssembler.save(self.root, CollectionAssembler().assemble(self.root, "g", ["t/nhi"]))
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

    def test_snapshot_provenance_must_match_accepted_results(self):
        nhi = [metadata_result("huynh-nhi", "2409.03594"), metadata_result("huynh-nhi", "2409.03595", commit=None)]
        runs = FakeRunReader({"nhi-run": ["huynh-nhi"]}, accepted={"nhi-run": nhi})
        cases = {"producer_commits": dict(producer_commits=[], unknown_provenance_tasks=1),
                 "unknown_provenance_tasks": dict(producer_commits=[COMMIT], unknown_provenance_tasks=0)}
        for field, manifest in {**cases, None: dict(producer_commits=[COMMIT], unknown_provenance_tasks=1)}.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                write_snapshot(tmp, "s/nhi", "nhi", ["huynh-nhi"], **manifest)
                CollectionAssembler.save(tmp, CollectionAssembler().assemble(tmp, "g", ["s/nhi"]))
                if field is None:
                    with CollectionDirectoryReader(runs).open(Path(tmp)) as collection:
                        self.assertEqual(len(collection.runs), 1)
                    continue
                with self.assertRaisesRegex(ValueError, "differs from its verified run: " + field):
                    with CollectionDirectoryReader(runs).open(Path(tmp)):
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


class ReportTests(unittest.TestCase):
    def test_collection_report_lists_issues_and_missing_owners(self):
        from dataclasses import replace
        from arxiv_pipeline.collection.report import collection_report, run_report
        issue = {"owner": "huynh-nhi", "arxiv_id": "2409.03594", "version": None, "step": "references",
                 "field": None, "message": "References/pages or reference metadata incomplete"}
        with tempfile.TemporaryDirectory() as tmp:
            write_snapshot(tmp, "s/nhi", "nhi", ["huynh-nhi"])
            write_snapshot(tmp, "s/rest", "rest", ["khang", "trong", "hoang", "man-nhi"])
            CollectionAssembler.save(tmp, CollectionAssembler().assemble(tmp, "g", ["s/nhi", "s/rest"]))
            runs = FakeRunReader({"nhi-run": ["huynh-nhi"], "rest-run": ["khang", "trong", "hoang", "man-nhi"]})
            with CollectionDirectoryReader(runs).open(Path(tmp)) as collection:
                report, code = collection_report(collection)
                self.assertEqual((code, report["missing_owners"], report["issues"]), (0, [], []))
                broken = replace(collection.runs[0], quality={"valid": False, "issues": [issue]})
                report, code = collection_report(replace(collection, runs=[broken, collection.runs[1]]))
                self.assertEqual(code, 2)
                self.assertEqual(report["issues"], [{"snapshot_id": "nhi", **{k: issue[k] for k in
                                 ("owner", "arxiv_id", "version", "step", "message")}}])
                self.assertEqual(run_report(broken)[1], 2)


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
            CollectionAssembler.save(root, CollectionAssembler().assemble(root, "g", ["s/nhi", "s/trong"]))
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
                (root / "collection.json").unlink(missing_ok=True)
                CollectionAssembler.save(root, CollectionAssembler().assemble(root, "g", members))
                runs = FakeRunReader({"nhi-run": ["huynh-nhi"], "rest-run": ["khang", "trong", "hoang", "man-nhi"]},
                                     discovery_complete=True)
                with CollectionDirectoryReader(runs).open(root) as collection:
                    stats = CollectionStatisticsBuilder(FakeSummarizer({})).build(collection)
                with self.subTest(members=members):
                    # Huỳnh Nhi's run alone finished discovery, but four ranges are still missing.
                    self.assertEqual(stats["scope"]["discovery_complete"], len(members) == 2)

    def test_versions_complete_comes_from_metadata_of_merged_papers(self):
        papers = {"nhi-run": [("huynh-nhi", "2409.03594"), ("huynh-nhi", "2409.03595")],
                  "trong-run": [("trong", "2504.17523")]}
        nhi = [metadata_result("huynh-nhi", "2409.03594"), metadata_result("huynh-nhi", "2409.03595")]
        cases = {"all verified": ([metadata_result("trong", "2504.17523")], True),
                 "one history unverified": ([metadata_result("trong", "2504.17523", False)], False),
                 "one paper without metadata": ([], False)}
        for name, (trong, expected) in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                write_snapshot(root, "s/nhi", "nhi", ["huynh-nhi"], producer_commits=[COMMIT])
                write_snapshot(root, "s/trong", "trong", ["trong"], producer_commits=[COMMIT] if trong else [])
                CollectionAssembler.save(root, CollectionAssembler().assemble(root, "g", ["s/nhi", "s/trong"]))
                # Each run's own flag says the opposite; the merged scope must not copy it.
                runs = FakeRunReader({"nhi-run": ["huynh-nhi"], "trong-run": ["trong"]}, papers,
                                     accepted={"nhi-run": nhi, "trong-run": trong})
                values = {"huynh-nhi": summary(2, 2), "trong": summary(1, 1)}
                with CollectionDirectoryReader(runs).open(root) as collection:
                    stats = CollectionStatisticsBuilder(FakeSummarizer(values)).build(collection)
                self.assertIs(stats["scope"]["versions_complete"], expected)

    def test_missing_mean_for_sampled_owner_is_an_error(self):
        with self.assertRaises(ValueError):
            combine_summaries([summary(1, 1, samples=3, mean=None)])


class FakeSnapshotReader:
    """Stands in for Khang's SnapshotReader: an "archive" here is a folder to copy."""

    def __init__(self):
        self.unpacked = []

    def unpack(self, archive, destination):
        shutil.copytree(archive, destination)
        self.unpacked.append(Path(archive).name)
        return json.loads((Path(destination) / "manifest.json").read_text())


class CollectionServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.archives = Path(self.tmp.name) / "archives"
        self.root = Path(self.tmp.name) / "collection"
        write_snapshot(self.archives, "nhi.tar", "nhi-1", ["huynh-nhi"])
        write_snapshot(self.archives, "trong.tar", "trong-1", ["trong"])
        write_snapshot(self.archives, "nhi-again.tar", "nhi-2", ["huynh-nhi"])
        self.runs = FakeRunReader({"nhi-1-run": ["huynh-nhi"], "trong-1-run": ["trong"], "nhi-2-run": ["huynh-nhi"]})
        self.service = CollectionService(FakeSnapshotReader(), self.runs,
                                         assembler=CollectionAssembler(clock=lambda: "2026-10-30T00:00:00Z"))

    def tearDown(self):
        self.tmp.cleanup()

    def create(self, *names):
        return self.service.create([self.archives / n for n in names], self.root, "group")

    def test_creates_verified_collection_from_archives(self):
        manifest = self.create("nhi.tar", "trong.tar")
        self.assertEqual([(m["snapshot_id"], m["root"]) for m in manifest["members"]],
                         [("nhi-1", "snapshots/nhi-1/corpus"), ("trong-1", "snapshots/trong-1/corpus")])
        self.assertEqual(json.loads((self.root / "collection.json").read_text()), manifest)
        self.assertEqual(sorted(self.runs.opened), ["nhi-1-run", "trong-1-run"])  # every run was verified
        with CollectionDirectoryReader(self.runs).open(self.root) as collection:
            self.assertEqual(len(collection.runs), 2)

    def test_errors_leave_no_collection(self):
        cases = {"two snapshots of one owner": ("nhi.tar", "nhi-again.tar"),
                 "same archive twice": ("nhi.tar", "nhi.tar")}
        for name, archives in cases.items():
            with self.subTest(name):
                shutil.rmtree(self.root, ignore_errors=True)
                with self.assertRaises(ValueError):
                    self.create(*archives)
                self.assertFalse((self.root / "collection.json").exists())
        shutil.rmtree(self.root, ignore_errors=True)
        moved = FakeRunReader({"nhi-1-run": ["huynh-nhi"]}, origin="/other/out")
        with self.assertRaisesRegex(ValueError, "origin_output_root"):
            CollectionService(FakeSnapshotReader(), moved).create([self.archives / "nhi.tar"], self.root, "g")
        self.assertFalse((self.root / "collection.json").exists())

    def test_does_not_replace_an_existing_collection(self):
        self.create("nhi.tar")
        before = (self.root / "collection.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.create("trong.tar")
        self.assertEqual((self.root / "collection.json").read_bytes(), before)
        self.assertFalse((self.root / "snapshots/trong-1").exists())  # nothing unpacked either


if __name__ == "__main__":
    unittest.main()
