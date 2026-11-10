import unittest
from arxiv_pipeline.parsing.contracts import (
    GatheredSource,
    ElementRecord,
    HierarchyEdgeRecord,
    BibtexRecord,
    PublicationParsingResult,
    LocalNode,
    VersionHierarchy,
)


class TestParsingContracts(unittest.TestCase):
    def test_gathered_source_fields(self):
        gathered: GatheredSource = {
            "arxiv_id": "2409.03594",
            "version": 1,
            "root_file": "main.tex",
            "compilation_path": ["main.tex"],
            "merged_tex": r"\documentclass{article}",
            "bib_files": ["data/2409-03594/v1/refs.bib"],
            "bbl_files": [],
            "status": "ok",
        }
        self.assertEqual(gathered["bib_files"], ["data/2409-03594/v1/refs.bib"])
        self.assertEqual(gathered["bbl_files"], [])
        self.assertEqual(gathered["status"], "ok")

    def test_element_record_fields(self):
        record: ElementRecord = {
            "element_id": "2409.03594#s1",
            "arxiv_id": "2409.03594",
            "kind": "sentence",
            "content": "We study citation matching.",
        }
        self.assertEqual(record["element_id"], "2409.03594#s1")
        self.assertEqual(record["kind"], "sentence")

    def test_hierarchy_record_fields(self):
        edge: HierarchyEdgeRecord = {
            "arxiv_id": "2409.03594",
            "version": 1,
            "element_id": "2409.03594#s1",
            "parent_id": "2409.03594#h1",
        }
        self.assertEqual(edge["version"], 1)
        self.assertEqual(edge["parent_id"], "2409.03594#h1")

    def test_bibtex_record_fields(self):
        bib: BibtexRecord = {
            "arxiv_id": "2409.03594",
            "citation_key": "vaswani2017",
            "entry_type": "article",
            "title": "Attention Is All You Need",
            "authors": ["Ashish Vaswani", "Noam Shazeer"],
            "year": 2017,
            "doi": "10.5555/3295222.3295349",
            "journal": "NeurIPS",
            "url": "https://arxiv.org/abs/1706.03762",
        }
        self.assertEqual(bib["citation_key"], "vaswani2017")
        self.assertEqual(len(bib["authors"]), 2)
        self.assertEqual(bib["year"], 2017)


if __name__ == "__main__":
    unittest.main()
