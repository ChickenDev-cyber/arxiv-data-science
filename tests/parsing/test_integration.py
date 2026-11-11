import tempfile
import unittest
from pathlib import Path
from arxiv_pipeline.parsing.runner import run_parsing_pipeline
from arxiv_pipeline.parsing.storage import ParsingDatabase


class TestParsingIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.corpus_path = self.base_path / "corpus"
        self.db_path = self.base_path / "integrated.sqlite"

        # Paper 1: Multi-version paper with shared sentences and bibitem
        p1 = self.corpus_path / "2409.00001"
        (p1 / "sources" / "v1").mkdir(parents=True, exist_ok=True)
        (p1 / "sources" / "v2").mkdir(parents=True, exist_ok=True)
        (p1 / "sources" / "v1" / "main.tex").write_text(r"""
        \documentclass{article}
        \begin{document}
        \section{Intro}
        Shared sentence across versions. Version one unique sentence.
        \begin{thebibliography}{1}
        \bibitem{p1_ref} Title One. 2021.
        \end{thebibliography}
        \end{document}
        """, encoding="utf-8")
        (p1 / "sources" / "v2" / "main.tex").write_text(r"""
        \documentclass{article}
        \begin{document}
        \section{Intro}
        Shared sentence across versions. Version two unique sentence.
        \end{document}
        """, encoding="utf-8")

        # Paper 2: PDF-only empty paper
        p2 = self.corpus_path / "2409.00002"
        (p2 / "sources" / "v1").mkdir(parents=True, exist_ok=True)  # empty folder

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_full_pipeline_deduplication_and_integrity(self):
        """End-to-end integration verifying deduplication, foreign relationships, and empty version handling."""
        stats = run_parsing_pipeline(self.corpus_path, self.db_path)
        self.assertEqual(stats["total_publications"], 2)
        self.assertEqual(stats["processed_publications"], 2)

        db = ParsingDatabase(self.db_path)
        try:
            # 1. Shared sentence deduplication
            p1_elements = db.get_elements("2409.00001")
            shared_matches = [e for e in p1_elements if "Shared sentence across versions" in e["content"]]
            self.assertEqual(len(shared_matches), 1, "Shared sentence must exist only once in elements table")
            shared_id = shared_matches[0]["element_id"]

            # 2. Hierarchy edges point to shared element in both v1 and v2
            p1_edges = db.get_hierarchy("2409.00001")
            v1_edges = [e for e in p1_edges if e["version"] == 1 and e["element_id"] == shared_id]
            v2_edges = [e for e in p1_edges if e["version"] == 2 and e["element_id"] == shared_id]
            self.assertEqual(len(v1_edges), 1)
            self.assertEqual(len(v2_edges), 1)

            # 3. BibTeX extraction
            p1_bib = db.get_bibtex("2409.00001")
            self.assertEqual(len(p1_bib), 1)
            self.assertEqual(p1_bib[0]["citation_key"], "p1_ref")

            # 4. Empty paper handling
            p2_elements = db.get_elements("2409.00002")
            self.assertEqual(len(p2_elements), 0)

            # 5. Database integrity and foreign key consistency
            fk_violations = db.conn.execute("PRAGMA foreign_key_check").fetchall()
            self.assertEqual(len(fk_violations), 0, f"Foreign key violations found: {fk_violations}")
            integrity = [row[0] for row in db.conn.execute("PRAGMA integrity_check").fetchall()]
            self.assertEqual(integrity, ["ok"])
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
