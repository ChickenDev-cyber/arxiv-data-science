import json
import tempfile
import unittest
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from arxiv_pipeline.matching.data import (
    SCHEMAS, in_owner_range, load_inventory, make_sample, prepare_inputs, read_table,
)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def write_m1(corpus, owner, papers):
    """papers: {arxiv_id: (metadata | None, references | None)}"""
    for aid, (meta, refs) in papers.items():
        folder = corpus / "raw" / owner / aid.replace("/", "__").replace(".", "-")
        folder.mkdir(parents=True, exist_ok=True)
        if meta is not None:
            write_json(folder / "metadata.json", meta)
        if refs is not None:
            write_json(folder / "references.json", refs)


def m2_tables(rows):
    """Write M2-style Parquet (plain string kind/entry_type, int32) like parsing/storage.py."""
    out = {}
    out["elements"] = pa.table({
        "element_id": pa.array([r[0] for r in rows["elements"]], pa.string()),
        "arxiv_id": pa.array([r[1] for r in rows["elements"]], pa.string()),
        "kind": pa.array([r[2] for r in rows["elements"]], pa.string()),
        "content": pa.array([r[3] for r in rows["elements"]], pa.string()),
    })
    out["hierarchy"] = pa.table({
        "arxiv_id": pa.array([r[0] for r in rows["hierarchy"]], pa.string()),
        "version": pa.array([r[1] for r in rows["hierarchy"]], pa.int32()),
        "element_id": pa.array([r[2] for r in rows["hierarchy"]], pa.string()),
        "parent_id": pa.array([r[3] for r in rows["hierarchy"]], pa.string()),
    })
    b = rows["bibtex"]
    out["bibtex"] = pa.table({
        "arxiv_id": pa.array([r[0] for r in b], pa.string()),
        "citation_key": pa.array([r[1] for r in b], pa.string()),
        "entry_type": pa.array([r[2] for r in b], pa.string()),
        "title": pa.array([r[3] for r in b], pa.string()),
        "authors": pa.array([r[4] for r in b], pa.list_(pa.string())),
        "year": pa.array([r[5] for r in b], pa.int32()),
        "doi": pa.array([r[6] for r in b], pa.string()),
        "journal": pa.array([r[7] for r in b], pa.string()),
        "url": pa.array([r[8] for r in b], pa.string()),
    })
    return out


def write_m2(parquet_dir, rows):
    for name, table in m2_tables(rows).items():
        (parquet_dir / name).mkdir(parents=True, exist_ok=True)
        pq.write_table(table, parquet_dir / name / "part-00000.parquet")


META = {"arxiv_id": "2409.03594", "title": "A paper", "authors": ["A. One", "B. Two"],
        "submission_date": "2024-09-05", "revised_dates": ["2024-10-01"], "venue": None,
        "versions": [{"version": 1, "submitted_at": "2024-09-05"}], "versions_complete": True}
REFS = {"1706.03762": {"arxiv_id": "1706.03762", "title": "Attention", "authors": ["A. Vaswani"],
                       "submission_date": "2017-06-12", "semantic_scholar_id": "s2-1"},
        "hep-th/9901001": {"arxiv_id": "hep-th/9901001", "title": None, "authors": None,
                           "submission_date": None, "semantic_scholar_id": None}}
M2_ROWS = {
    "elements": [("e-doc", "2409.03594", "document", "doc"), ("e-s1", "2409.03594", "sentence", "hi"),
                 ("e-doc2", "2409.03595v2", "document", "doc2")],
    "hierarchy": [("2409.03594", 1, "e-s1", "e-doc"), ("2409.03595", 2, "e-doc2", "root")],
    "bibtex": [("2409.03594", "vaswani2017", "article", "Attention", ["A. Vaswani"], 2017, None, None,
                "https://arxiv.org/abs/1706.03762"),
               ("2409.03594", "foo", "misc", None, [], None, None, None, None)],
}


class PrepareInputsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        corpus = self.root / "m1" / "corpus"
        write_m1(corpus, "huynh-nhi", {
            "2409.03594": (META, REFS),
            "2409.03595": (None, {}),                  # metadata missing, references empty
            "2409.03596": ({**META, "arxiv_id": "2409.03596", "authors": None}, None),
        })
        write_json(self.root / "m1" / "manifest.json", {"run_id": "m1-colab-huynh-nhi-01"})
        m2 = self.root / "m2" / "run-01" / "artifacts" / "result-1" / "parquet"
        write_m2(m2, M2_ROWS)
        write_json(self.root / "m2" / "run-01" / "handoff.json", {"run_id": "m2-huynh-nhi-01"})
        self.config = self.root / "sources.json"
        write_json(self.config, {"owners": {
            "huynh-nhi": {"m1_corpus": "m1/corpus", "m2_parquet": str(m2)},
            "hoang": {"m1_corpus": "does/not/exist"},
        }})
        self.out = self.root / "run"
        self.inv_path = prepare_inputs(self.config, self.out)
        self.inv = json.loads(self.inv_path.read_text())

    def tearDown(self):
        self.tmp.cleanup()

    def owner(self, name="huynh-nhi"):
        return self.inv["owners"][name]

    def test_layout_and_schema(self):
        info = self.owner()
        for table, schema in SCHEMAS.items():
            paths = info["tables"][table]
            self.assertTrue(paths, table)
            for p in paths:
                self.assertTrue(p.startswith(f"inputs/huynh-nhi/{table}/part-"))
                self.assertTrue(pq.read_schema(self.out / p).equals(schema, check_metadata=False), table)

    def test_counts_and_n(self):
        info = self.owner()
        self.assertEqual(info["n_official_papers"], 3)
        self.assertEqual(info["row_counts"], {"papers": 3, "references": 2, "elements": 3,
                                              "hierarchy": 2, "bibtex": 2})
        self.assertEqual(info["sources"]["m1"]["label"], "m1-colab-huynh-nhi-01")
        self.assertEqual(info["sources"]["m2"]["label"], "m2-huynh-nhi-01")

    def test_missing_metadata_stays_null(self):
        papers = read_table(self.inv_path, "papers", "huynh-nhi").to_pylist()
        by_id = {p["arxiv_id"]: p for p in papers}
        self.assertIsNone(by_id["2409.03595"]["title"])
        self.assertIsNone(by_id["2409.03595"]["revised_dates"])
        self.assertIsNone(by_id["2409.03596"]["authors"])
        self.assertEqual(by_id["2409.03594"]["authors"], ["A. One", "B. Two"])

    def test_references_keep_old_ids_and_citing_paper(self):
        refs = read_table(self.inv_path, "references").to_pylist()
        self.assertEqual({r["ref_arxiv_id"] for r in refs}, {"1706.03762", "hep-th/9901001"})
        self.assertTrue(all(r["arxiv_id"] == "2409.03594" for r in refs))
        codes = {i["code"] for i in self.owner()["table_details"]["references"]["issues"]}
        self.assertIn("references_empty", codes)
        self.assertIn("references_missing", codes)

    def test_version_suffix_removed_and_dictionary_types(self):
        elements = read_table(self.inv_path, "elements")
        self.assertIn("2409.03595", elements.column("arxiv_id").to_pylist())
        self.assertTrue(pa.types.is_dictionary(elements.schema.field("kind").type))
        bib = read_table(self.inv_path, "bibtex")
        self.assertTrue(pa.types.is_dictionary(bib.schema.field("entry_type").type))
        self.assertEqual(bib.column("year").to_pylist(), [2017, None])

    def test_missing_owner_source_is_reported(self):
        hoang = self.owner("hoang")
        self.assertEqual(hoang["table_status"]["elements"], "missing")
        self.assertIn("m1_not_found", {i["code"] for i in hoang["issues"]})
        self.assertNotEqual(hoang["status"], "ok")

    def test_parent_not_in_elements_is_warning(self):
        codes = {i["code"]: i["level"] for i in self.owner()["table_details"]["hierarchy"]["issues"]}
        self.assertEqual(codes.get("parent_not_in_elements"), "warning")

    def test_refuses_to_overwrite(self):
        with self.assertRaises(FileExistsError):
            prepare_inputs(self.config, self.out)

    def test_refuses_output_inside_source(self):
        with self.assertRaises(ValueError):
            prepare_inputs(self.config, self.root / "m1" / "corpus" / "out")

    def test_load_inventory_resolves_paths(self):
        data = load_inventory(self.inv_path)
        for p in data["owners"]["huynh-nhi"]["tables"]["papers"]:
            self.assertTrue(Path(p).is_absolute() and Path(p).is_file())

    def test_sample(self):
        sample = make_sample(self.inv_path, self.root / "sample", papers_per_owner=1)
        data = json.loads(sample.read_text())
        info = data["owners"]["huynh-nhi"]
        self.assertEqual(info["sample_arxiv_ids"], ["2409.03594"])  # has bibtex and refs
        self.assertEqual(info["row_counts"]["bibtex"], 2)
        papers = read_table(sample, "papers").to_pylist()
        self.assertEqual([p["arxiv_id"] for p in papers], ["2409.03594"])


class ValidationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_with(self, rows, papers=None):
        corpus = self.root / "corpus"
        write_m1(corpus, "huynh-nhi", papers or {"2409.03594": (META, REFS)})
        write_m2(self.root / "pq", rows)
        cfg = self.root / "cfg.json"
        write_json(cfg, {"owners": {"huynh-nhi": {"m1_corpus": str(corpus), "m2_parquet": str(self.root / "pq")}}})
        return json.loads(prepare_inputs(cfg, self.root / "out").read_text())["owners"]["huynh-nhi"]

    def codes(self, info, table):
        return {i["code"]: i for i in info["table_details"][table]["issues"]}

    def test_foreign_owner_rows_dropped(self):
        rows = {**M2_ROWS, "bibtex": M2_ROWS["bibtex"] + [
            ("2503.06914", "x", "article", None, None, None, None, None, None)]}
        info = self.run_with(rows)
        self.assertEqual(info["row_counts"]["bibtex"], 2)
        self.assertIn("foreign_owner", self.codes(info, "bibtex"))
        self.assertEqual(info["table_status"]["bibtex"], "error")

    def test_duplicates(self):
        dup = M2_ROWS["bibtex"][0]
        conflict = (*dup[:3], "Other title", *dup[4:])
        info = self.run_with({**M2_ROWS, "bibtex": M2_ROWS["bibtex"] + [dup, conflict]})
        codes = self.codes(info, "bibtex")
        self.assertEqual(codes["duplicate_identical"]["level"], "warning")
        self.assertEqual(codes["duplicate_conflict"]["level"], "error")
        self.assertEqual(info["row_counts"]["bibtex"], 2)

    def test_null_key_dropped(self):
        rows = {**M2_ROWS, "bibtex": M2_ROWS["bibtex"] + [
            ("2409.03594", None, "misc", None, None, None, None, None, None)]}
        info = self.run_with(rows)
        self.assertIn("null_citation_key", self.codes(info, "bibtex"))
        self.assertEqual(info["row_counts"]["bibtex"], 2)

    def test_empty_table_distinguished(self):
        info = self.run_with({**M2_ROWS, "bibtex": []})
        self.assertEqual(info["table_status"]["bibtex"], "empty")
        self.assertEqual(info["row_counts"]["bibtex"], 0)

    def test_missing_m2_table(self):
        write_m2(self.root / "pq", M2_ROWS)
        (self.root / "pq" / "bibtex" / "part-00000.parquet").unlink()
        (self.root / "pq" / "bibtex").rmdir()
        corpus = self.root / "corpus"
        write_m1(corpus, "huynh-nhi", {"2409.03594": (META, REFS)})
        cfg = self.root / "cfg.json"
        write_json(cfg, {"owners": {"huynh-nhi": {"m1_corpus": str(corpus), "m2_parquet": str(self.root / "pq")}}})
        info = json.loads(prepare_inputs(cfg, self.root / "out").read_text())["owners"]["huynh-nhi"]
        self.assertEqual(info["table_status"]["bibtex"], "missing")
        self.assertIsNone(info["row_counts"]["bibtex"])

    def test_range(self):
        self.assertTrue(in_owner_range("huynh-nhi", "2409.03594"))
        self.assertTrue(in_owner_range("huynh-nhi", "2409.08593"))
        self.assertFalse(in_owner_range("huynh-nhi", "2409.08594"))
        self.assertTrue(in_owner_range("khang", "2507.01000"))
        self.assertFalse(in_owner_range("khang", "2506.20787"))



class DriveLayoutTest(unittest.TestCase):
    """M1 as the Drive zip (read in place) and M2 as milestone 2/<owner>/<run>/handoff.json."""

    def setUp(self):
        import hashlib
        import zipfile
        self.tmp = tempfile.TemporaryDirectory()
        root = self.root = Path(self.tmp.name)
        corpus = root / "extract" / "m1-colab-huynh-nhi-01" / "corpus"
        write_m1(corpus, "huynh-nhi", {"2409.03594": (META, REFS), "2409.03595": (None, {})})
        write_m1(corpus, "trong", {"2504.17803": ({**META, "arxiv_id": "2504.17803"}, {})})
        self.zip = root / "Full-data" / "m1-colab-huynh-nhi-01.zip"
        self.zip.parent.mkdir()
        with zipfile.ZipFile(self.zip, "w") as z:
            for f in sorted((root / "extract").rglob("*")):
                if f.is_file():
                    z.write(f, f.relative_to(root / "extract").as_posix())
        self.run_dir = root / "milestone 2" / "huynh-nhi" / "fresh-01"
        gen = self.run_dir / "artifacts" / "result-abc"
        write_m2(gen / "parquet", M2_ROWS)
        write_m2(self.run_dir / "artifacts" / "result-old" / "parquet", {**M2_ROWS, "bibtex": []})
        sha = {f"parquet/{t}/part-00000.parquet":
               hashlib.sha256((gen / "parquet" / t / "part-00000.parquet").read_bytes()).hexdigest()
               for t in ("elements", "hierarchy", "bibtex")}
        write_json(self.run_dir / "handoff.json", {
            "status": "complete", "source_sha256": "abc",
            "directory": "/content/drive/x/milestone 2/huynh-nhi/fresh-01/artifacts/result-abc", "sha256": sha})
        write_json(self.run_dir / "run.json", {"run_id": "fresh-01", "owner": "huynh-nhi"})
        self.cfg = root / "cfg.json"
        write_json(self.cfg, {"owners": {"huynh-nhi": {
            "m1_corpus": "Full-data/m1-colab-huynh-nhi-01.zip", "m2_run_dir": "milestone 2/huynh-nhi/fresh-01"}}})

    def tearDown(self):
        self.tmp.cleanup()

    def test_zip_and_handoff(self):
        info = json.loads(prepare_inputs(self.cfg, self.root / "out").read_text())["owners"]["huynh-nhi"]
        self.assertEqual(info["status"], "ok", info)
        self.assertEqual(info["n_official_papers"], 2)  # trong's folder in the same zip is ignored
        self.assertEqual(info["row_counts"]["bibtex"], 2)  # handoff generation, not result-old
        self.assertEqual(info["sources"]["m1"]["label"], "m1-colab-huynh-nhi-01")
        self.assertEqual(info["sources"]["m2"]["label"], "fresh-01")
        self.assertEqual(info["sources"]["m2"]["resolved"]["checksums_verified"], 3)

    def test_checksum_mismatch_is_error(self):
        target = self.run_dir / "artifacts" / "result-abc" / "parquet" / "bibtex" / "part-00000.parquet"
        target.write_bytes(target.read_bytes() + b"x")
        info = json.loads(prepare_inputs(self.cfg, self.root / "out").read_text())["owners"]["huynh-nhi"]
        self.assertIn("m2_checksum_mismatch", {i["code"] for i in info["issues"]})
        self.assertNotEqual(info["status"], "ok")

    def test_missing_handoff_falls_back_with_error(self):
        (self.run_dir / "handoff.json").unlink()
        info = json.loads(prepare_inputs(self.cfg, self.root / "out").read_text())["owners"]["huynh-nhi"]
        self.assertIn("m2_handoff_missing", {i["code"] for i in info["issues"]})
        self.assertEqual(info["status"], "incomplete")


class ScanDriveTest(unittest.TestCase):
    def test_scan(self):
        from arxiv_pipeline.matching.data import scan_drive
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Full-data").mkdir()
            (root / "Full-data" / "m1-colab-hoang-01.zip").write_bytes(b"")
            write_json(root / "milestone 2" / "hoang" / "fresh-01" / "handoff.json", {"status": "complete"})
            (root / "milestone 2" / "hoang" / "broken-02").mkdir()
            cfg = scan_drive(root)
            self.assertTrue(cfg["owners"]["hoang"]["m2_run_dir"].endswith("fresh-01"))
            self.assertTrue(cfg["owners"]["hoang"]["m1_corpus"].endswith("m1-colab-hoang-01.zip"))
            self.assertEqual(cfg["owners"]["trong"], {})
            self.assertTrue(cfg["notes"]["trong"])


class ReviewFixesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        corpus = self.root / "corpus"
        write_m1(corpus, "huynh-nhi", {
            "2409.03594": (META, REFS),
            "2409.03597": ({**META, "arxiv_id": "2409.03597", "revised_dates": None}, {}),
            "2409.03598": ({**META, "arxiv_id": "2409.03598", "revised_dates": []}, {}),
        })
        write_m2(self.root / "pq", M2_ROWS)
        self.cfg = self.root / "cfg.json"
        write_json(self.cfg, {"owners": {"huynh-nhi": {"m1_corpus": str(corpus), "m2_parquet": str(self.root / "pq")}}})

    def tearDown(self):
        self.tmp.cleanup()

    def test_revised_dates_null_stays_null(self):
        inv = prepare_inputs(self.cfg, self.root / "out")
        by_id = {p["arxiv_id"]: p for p in read_table(inv, "papers").to_pylist()}
        self.assertIsNone(by_id["2409.03597"]["revised_dates"])
        self.assertEqual(by_id["2409.03598"]["revised_dates"], [])
        self.assertEqual(by_id["2409.03594"]["revised_dates"], ["2024-10-01"])

    def test_bibtex_authors_is_list(self):
        inv = prepare_inputs(self.cfg, self.root / "out")
        field = read_table(inv, "bibtex").schema.field("authors")
        self.assertTrue(pa.types.is_list(field.type))

    def test_cli_prepare_sample_report(self):
        import contextlib
        import io
        from arxiv_pipeline.matching.data import main
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["prepare", "--config", str(self.cfg), "--output-dir", str(self.root / "out")])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("huynh-nhi", out.getvalue())
        inv = self.root / "out" / "inventory.json"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["sample", "--inventory", str(inv), "--output-dir", str(self.root / "s")]), 0)
            self.assertEqual(main(["report", "--inventory", str(inv)]), 0)
        with contextlib.redirect_stderr(io.StringIO()):
            # second prepare into the same folder is refused with exit 1
            self.assertEqual(main(["prepare", "--config", str(self.cfg), "--output-dir", str(self.root / "out")]), 1)

    def test_cli_exit_2_when_owner_incomplete(self):
        import contextlib
        import io
        from arxiv_pipeline.matching.data import main
        write_json(self.cfg, {"owners": {"huynh-nhi": {"m1_corpus": str(self.root / "corpus")}}})
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["prepare", "--config", str(self.cfg), "--output-dir", str(self.root / "o2")]), 2)

    def test_cli_scan_refuses_overwrite(self):
        import contextlib
        import io
        from arxiv_pipeline.matching.data import main
        target = self.root / "draft.json"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["scan", "--drive-root", str(self.root), "--write", str(target)]), 0)
        self.assertTrue(target.is_file())
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["scan", "--drive-root", str(self.root), "--write", str(target)]), 1)


if __name__ == "__main__":
    unittest.main()
