import unittest
from arxiv_pipeline.parsing.contracts import (
    VersionHierarchy,
    LocalNode,
    PaperReferences,
    BibtexEntry,
)
from arxiv_pipeline.parsing.dedup import deduplicate_publication_elements


class TestDedupEngine(unittest.TestCase):
    def setUp(self):
        self.arxiv_id = "2409.03594"

    def test_shared_sentence_deduplication(self):
        """Review Focus 2 & 3: Sentence appearing in v1 and v2 shares element_id."""
        v1_nodes: list[LocalNode] = [
            {"temp_id": "root", "kind": "document", "content": "Root V1", "parent_temp_id": None},
            {"temp_id": "h1", "kind": "section", "content": "Introduction", "parent_temp_id": "root"},
            {"temp_id": "s1", "kind": "sentence", "content": "We study citation matching.", "parent_temp_id": "h1"},
        ]
        v2_nodes: list[LocalNode] = [
            {"temp_id": "root", "kind": "document", "content": "Root V2", "parent_temp_id": None},
            {"temp_id": "h2", "kind": "section", "content": "Background", "parent_temp_id": "root"},
            {"temp_id": "s_same", "kind": "sentence", "content": "We study citation matching.", "parent_temp_id": "h2"},
            {"temp_id": "s_new", "kind": "sentence", "content": "A brand new sentence.", "parent_temp_id": "h2"},
        ]

        h1: VersionHierarchy = {"arxiv_id": self.arxiv_id, "version": 1, "nodes": v1_nodes}
        h2: VersionHierarchy = {"arxiv_id": self.arxiv_id, "version": 2, "nodes": v2_nodes}

        res = deduplicate_publication_elements(self.arxiv_id, [h1, h2], None)

        # Content "We study citation matching." must appear exactly once in elements
        matching_elements = [e for e in res["elements"] if e["content"] == "We study citation matching."]
        self.assertEqual(len(matching_elements), 1)
        shared_id = matching_elements[0]["element_id"]

        # Hierarchy must have 2 edges pointing to shared_id (one in v1, one in v2)
        v1_edge = next(e for e in res["hierarchy"] if e["version"] == 1 and e["element_id"] == shared_id)
        v2_edge = next(e for e in res["hierarchy"] if e["version"] == 2 and e["element_id"] == shared_id)

        self.assertNotEqual(v1_edge["parent_id"], v2_edge["parent_id"])
        self.assertTrue(v1_edge["parent_id"].startswith(f"{self.arxiv_id}#"))
        self.assertTrue(v2_edge["parent_id"].startswith(f"{self.arxiv_id}#"))

    def test_whitespace_insensitivity(self):
        """Review Focus 2: Elements differing only in trailing whitespace/newlines deduplicate."""
        v1_nodes: list[LocalNode] = [
            {"temp_id": "s1", "kind": "sentence", "content": "Theorem 1 holds.\n", "parent_temp_id": None},
        ]
        v2_nodes: list[LocalNode] = [
            {"temp_id": "s2", "kind": "sentence", "content": "  Theorem 1 holds.  \r\n", "parent_temp_id": None},
        ]
        h1: VersionHierarchy = {"arxiv_id": self.arxiv_id, "version": 1, "nodes": v1_nodes}
        h2: VersionHierarchy = {"arxiv_id": self.arxiv_id, "version": 2, "nodes": v2_nodes}

        res = deduplicate_publication_elements(self.arxiv_id, [h1, h2], None)
        self.assertEqual(len(res["elements"]), 1)
        self.assertEqual(res["elements"][0]["content"], "Theorem 1 holds.")

    def test_empty_version_preserved(self):
        """Review Focus 1: Version with empty nodes recorded in empty_versions list."""
        h1: VersionHierarchy = {"arxiv_id": self.arxiv_id, "version": 1, "nodes": []}
        res = deduplicate_publication_elements(self.arxiv_id, [h1], None, empty_versions=[1])
        self.assertEqual(res["elements"], [])
        self.assertEqual(res["hierarchy"], [])
        self.assertIn(1, res["empty_versions"])

    def test_bibtex_and_version_counts(self):
        """Test processing of paper references and total version counting."""
        v1_nodes: list[LocalNode] = [
            {"temp_id": "root", "kind": "document", "content": "Document 1", "parent_temp_id": None},
        ]
        h1: VersionHierarchy = {"arxiv_id": self.arxiv_id, "version": 1, "nodes": v1_nodes}
        refs: PaperReferences = {
            "arxiv_id": self.arxiv_id,
            "unified_bibtex": [
                {
                    "arxiv_id": self.arxiv_id,
                    "citation_key": "vaswani2017attention",
                    "entry_type": "article",
                    "title": "Attention Is All You Need",
                    "authors": ["A. Vaswani", "N. Shazeer"],
                    "year": 2017,
                    "doi": "10.48550/arXiv.1706.03762",
                    "journal": "NeurIPS",
                    "url": "https://arxiv.org/abs/1706.03762",
                }
            ],
            "citation_remap": {1: {"vaswani2017": "vaswani2017attention"}},
        }
        res = deduplicate_publication_elements(self.arxiv_id, [h1], refs, empty_versions=[2])
        self.assertEqual(res["version_count"], 2)
        self.assertEqual(res["empty_versions"], [2])
        self.assertEqual(len(res["bibtex"]), 1)
        self.assertEqual(res["bibtex"][0]["citation_key"], "vaswani2017attention")


if __name__ == "__main__":
    unittest.main()
