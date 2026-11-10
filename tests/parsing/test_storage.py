import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import sys

from arxiv_pipeline.parsing.storage import ParsingDatabase
from arxiv_pipeline.parsing.contracts import PublicationParsingResult


class TestParsingDatabase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_parsing.sqlite")
        self.db = ParsingDatabase(self.db_path)

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()

    def test_schema_creation_and_insertion(self):
        """Test tables creation and insertion of publication result."""
        result: PublicationParsingResult = {
            "arxiv_id": "2409.03594",
            "elements": [
                {"element_id": "2409.03594#root1", "arxiv_id": "2409.03594", "kind": "document", "content": "Root V1"},
                {"element_id": "2409.03594#s1", "arxiv_id": "2409.03594", "kind": "sentence", "content": "Citation study."},
            ],
            "hierarchy": [
                {"arxiv_id": "2409.03594", "version": 1, "element_id": "2409.03594#s1", "parent_id": "2409.03594#root1"}
            ],
            "bibtex": [
                {
                    "arxiv_id": "2409.03594",
                    "citation_key": "ref1",
                    "entry_type": "article",
                    "title": "A Title",
                    "authors": ["Author One", "Author Two"],
                    "year": 2020,
                    "doi": "10.1234/5678",
                    "journal": "JMLR",
                    "url": "https://example.com",
                }
            ],
            "version_count": 1,
            "empty_versions": [],
        }

        self.db.save_publication(result)
        self.db.commit()

        # Query verification
        elements = self.db.get_elements("2409.03594")
        self.assertEqual(len(elements), 2)
        self.assertEqual(elements[1]["element_id"], "2409.03594#s1")

        edges = self.db.get_hierarchy("2409.03594")
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["parent_id"], "2409.03594#root1")

        bibtex = self.db.get_bibtex("2409.03594")
        self.assertEqual(len(bibtex), 1)
        self.assertEqual(bibtex[0]["authors"], ["Author One", "Author Two"])
        self.assertEqual(bibtex[0]["year"], 2020)

        meta = self.db.get_publication_meta("2409.03594")
        self.assertIsNotNone(meta)
        self.assertEqual(meta["version_count"], 1)
        self.assertEqual(meta["empty_versions"], [])

    def test_deduplication_idempotency(self):
        """Inserting identical element_id does not throw error or create duplicates."""
        record = {"element_id": "2409.03594#s1", "arxiv_id": "2409.03594", "kind": "sentence", "content": "Test."}
        self.db.insert_elements([record])
        self.db.insert_elements([record])  # re-insert same ID
        self.db.commit()

        elements = self.db.get_elements("2409.03594")
        self.assertEqual(len(elements), 1)

    def test_export_to_parquet_missing_pyarrow(self):
        """Verify RuntimeError when pyarrow is not installed."""
        out_dir = os.path.join(self.temp_dir.name, "parquet_out")
        with patch.dict(sys.modules, {"pyarrow": None, "pyarrow.parquet": None}):
            with self.assertRaises(RuntimeError) as ctx:
                self.db.export_to_parquet(out_dir)
            self.assertIn("pyarrow is required", str(ctx.exception))

    def test_export_to_parquet_with_mock(self):
        """Verify Parquet export logic when pyarrow is available."""
        result: PublicationParsingResult = {
            "arxiv_id": "2409.03594",
            "elements": [
                {"element_id": "2409.03594#root1", "arxiv_id": "2409.03594", "kind": "document", "content": "Root V1"},
            ],
            "hierarchy": [
                {"arxiv_id": "2409.03594", "version": 1, "element_id": "2409.03594#root1", "parent_id": ""}
            ],
            "bibtex": [
                {
                    "arxiv_id": "2409.03594",
                    "citation_key": "ref1",
                    "entry_type": "article",
                    "title": "A Title",
                    "authors": ["Author One"],
                    "year": 2020,
                    "doi": "",
                    "journal": "",
                    "url": "",
                }
            ],
            "version_count": 1,
            "empty_versions": [],
        }
        self.db.save_publication(result)
        self.db.commit()

        mock_pa = MagicMock()
        mock_pq = MagicMock()
        mock_pa.parquet = mock_pq
        out_dir = os.path.join(self.temp_dir.name, "parquet_out")

        with patch.dict(sys.modules, {"pyarrow": mock_pa, "pyarrow.parquet": mock_pq}):
            counts = self.db.export_to_parquet(out_dir)
            self.assertEqual(counts["elements"], 1)
            self.assertEqual(counts["hierarchy"], 1)
            self.assertEqual(counts["bibtex"], 1)
            self.assertEqual(mock_pq.write_table.call_count, 3)

    def test_save_publication_with_empty_versions(self):
        """Verify storage and retrieval of publications with empty versions."""
        result: PublicationParsingResult = {
            "arxiv_id": "2409.09999",
            "elements": [],
            "hierarchy": [],
            "bibtex": [],
            "version_count": 2,
            "empty_versions": [2],
        }
        self.db.save_publication(result)
        self.db.commit()

        meta = self.db.get_publication_meta("2409.09999")
        self.assertIsNotNone(meta)
        self.assertEqual(meta["version_count"], 2)
        self.assertEqual(meta["empty_versions"], [2])

        self.assertIsNone(self.db.get_publication_meta("non_existent_id"))

    def test_export_to_parquet_empty_tables(self):
        """Verify export returns 0 counts without error when tables are empty."""
        mock_pa = MagicMock()
        mock_pq = MagicMock()
        mock_pa.parquet = mock_pq
        out_dir = os.path.join(self.temp_dir.name, "empty_parquet_out")

        with patch.dict(sys.modules, {"pyarrow": mock_pa, "pyarrow.parquet": mock_pq}):
            counts = self.db.export_to_parquet(out_dir)
            self.assertEqual(counts["elements"], 0)
            self.assertEqual(counts["hierarchy"], 0)
            self.assertEqual(counts["bibtex"], 0)
            self.assertEqual(mock_pq.write_table.call_count, 0)


if __name__ == "__main__":
    unittest.main()
