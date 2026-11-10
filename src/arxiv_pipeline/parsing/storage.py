"""Relational storage engine for parsed elements, hierarchy, and BibTeX tables."""

import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional

from arxiv_pipeline.parsing.contracts import (
    BibtexRecord,
    ElementRecord,
    HierarchyEdgeRecord,
    PublicationParsingResult,
)


class ParsingDatabase:
    """Manages SQLite storage and querying for Milestone 2 output tables."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute("PRAGMA journal_mode = WAL;")
        cursor.execute("PRAGMA synchronous = NORMAL;")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS elements (
                element_id TEXT PRIMARY KEY,
                arxiv_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                content TEXT NOT NULL
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_elements_arxiv ON elements(arxiv_id);")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hierarchy (
                arxiv_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                element_id TEXT NOT NULL,
                parent_id TEXT NOT NULL,
                PRIMARY KEY (arxiv_id, version, element_id, parent_id)
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_hierarchy_arxiv_ver ON hierarchy(arxiv_id, version);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_hierarchy_parent ON hierarchy(arxiv_id, parent_id);")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bibtex (
                arxiv_id TEXT NOT NULL,
                citation_key TEXT NOT NULL,
                entry_type TEXT NOT NULL,
                title TEXT,
                authors TEXT,
                year INTEGER,
                doi TEXT,
                journal TEXT,
                url TEXT,
                PRIMARY KEY (arxiv_id, citation_key)
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_bibtex_arxiv ON bibtex(arxiv_id);")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS publications_meta (
                arxiv_id TEXT PRIMARY KEY,
                version_count INTEGER NOT NULL,
                empty_versions TEXT NOT NULL
            );
        """)
        self.conn.commit()

    def insert_elements(self, records: List[ElementRecord]) -> None:
        cursor = self.conn.cursor()
        cursor.executemany(
            """
            INSERT OR IGNORE INTO elements (element_id, arxiv_id, kind, content)
            VALUES (?, ?, ?, ?);
            """,
            [(r["element_id"], r["arxiv_id"], r["kind"], r["content"]) for r in records],
        )

    def insert_hierarchy(self, records: List[HierarchyEdgeRecord]) -> None:
        cursor = self.conn.cursor()
        cursor.executemany(
            """
            INSERT OR IGNORE INTO hierarchy (arxiv_id, version, element_id, parent_id)
            VALUES (?, ?, ?, ?);
            """,
            [(r["arxiv_id"], r["version"], r["element_id"], r["parent_id"]) for r in records],
        )

    def insert_bibtex(self, records: List[BibtexRecord]) -> None:
        cursor = self.conn.cursor()
        cursor.executemany(
            """
            INSERT OR REPLACE INTO bibtex (arxiv_id, citation_key, entry_type, title, authors, year, doi, journal, url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            [
                (
                    r["arxiv_id"],
                    r["citation_key"],
                    r["entry_type"],
                    r.get("title", ""),
                    json.dumps(r.get("authors", []), ensure_ascii=False),
                    r.get("year"),
                    r.get("doi", ""),
                    r.get("journal", ""),
                    r.get("url", ""),
                )
                for r in records
            ],
        )

    def save_publication(self, result: PublicationParsingResult) -> None:
        self.insert_elements(result["elements"])
        self.insert_hierarchy(result["hierarchy"])
        self.insert_bibtex(result["bibtex"])

        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO publications_meta (arxiv_id, version_count, empty_versions)
            VALUES (?, ?, ?);
            """,
            (result["arxiv_id"], result["version_count"], json.dumps(result["empty_versions"])),
        )

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "ParsingDatabase":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def get_elements(self, arxiv_id: str) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT element_id, arxiv_id, kind, content FROM elements WHERE arxiv_id = ?",
            (arxiv_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_hierarchy(self, arxiv_id: str) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT arxiv_id, version, element_id, parent_id FROM hierarchy WHERE arxiv_id = ?",
            (arxiv_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_bibtex(self, arxiv_id: str) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT arxiv_id, citation_key, entry_type, title, authors, year, doi, journal, url FROM bibtex WHERE arxiv_id = ?",
            (arxiv_id,),
        )
        rows = []
        for r in cursor.fetchall():
            d = dict(r)
            d["authors"] = json.loads(d["authors"]) if d["authors"] else []
            rows.append(d)
        return rows

    def get_publication_meta(self, arxiv_id: str) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT arxiv_id, version_count, empty_versions FROM publications_meta WHERE arxiv_id = ?",
            (arxiv_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        d = dict(row)
        d["empty_versions"] = json.loads(d["empty_versions"]) if d["empty_versions"] else []
        return d

    def export_to_parquet(self, output_dir: Path | str) -> Dict[str, int]:
        """Export SQLite tables to Hugging Face-ready sharded Parquet files."""
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq

            if pa is None or pq is None:
                raise ImportError()
        except (ImportError, AttributeError):
            raise RuntimeError("pyarrow is required for Parquet export. Run pip install pyarrow.")

        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        counts: Dict[str, int] = {}
        cursor = self.conn.cursor()

        # Elements table
        cursor.execute("SELECT element_id, arxiv_id, kind, content FROM elements")
        el_rows = cursor.fetchall()
        if el_rows:
            table_el = pa.Table.from_arrays(
                [
                    pa.array([r[0] for r in el_rows], type=pa.string()),
                    pa.array([r[1] for r in el_rows], type=pa.string()),
                    pa.array([r[2] for r in el_rows], type=pa.string()),
                    pa.array([r[3] for r in el_rows], type=pa.string()),
                ],
                names=["element_id", "arxiv_id", "kind", "content"],
            )
            (out_path / "elements").mkdir(parents=True, exist_ok=True)
            pq.write_table(table_el, out_path / "elements" / "train.parquet", compression="snappy")
        counts["elements"] = len(el_rows)

        # Hierarchy table
        cursor.execute("SELECT arxiv_id, version, element_id, parent_id FROM hierarchy")
        h_rows = cursor.fetchall()
        if h_rows:
            table_h = pa.Table.from_arrays(
                [
                    pa.array([r[0] for r in h_rows], type=pa.string()),
                    pa.array([r[1] for r in h_rows], type=pa.int32()),
                    pa.array([r[2] for r in h_rows], type=pa.string()),
                    pa.array([r[3] for r in h_rows], type=pa.string()),
                ],
                names=["arxiv_id", "version", "element_id", "parent_id"],
            )
            (out_path / "hierarchy").mkdir(parents=True, exist_ok=True)
            pq.write_table(table_h, out_path / "hierarchy" / "train.parquet", compression="snappy")
        counts["hierarchy"] = len(h_rows)

        # Bibtex table (authors is typed as list<string>)
        cursor.execute(
            "SELECT arxiv_id, citation_key, entry_type, title, authors, year, doi, journal, url FROM bibtex"
        )
        b_rows = cursor.fetchall()
        if b_rows:
            parsed_authors = [json.loads(r[4]) if r[4] else [] for r in b_rows]
            table_b = pa.Table.from_arrays(
                [
                    pa.array([r[0] for r in b_rows], type=pa.string()),
                    pa.array([r[1] for r in b_rows], type=pa.string()),
                    pa.array([r[2] for r in b_rows], type=pa.string()),
                    pa.array([r[3] for r in b_rows], type=pa.string()),
                    pa.array(parsed_authors, type=pa.list_(pa.string())),
                    pa.array([r[5] for r in b_rows], type=pa.int32()),
                    pa.array([r[6] for r in b_rows], type=pa.string()),
                    pa.array([r[7] for r in b_rows], type=pa.string()),
                    pa.array([r[8] for r in b_rows], type=pa.string()),
                ],
                names=[
                    "arxiv_id",
                    "citation_key",
                    "entry_type",
                    "title",
                    "authors",
                    "year",
                    "doi",
                    "journal",
                    "url",
                ],
            )
            (out_path / "bibtex").mkdir(parents=True, exist_ok=True)
            pq.write_table(table_b, out_path / "bibtex" / "train.parquet", compression="snappy")
        counts["bibtex"] = len(b_rows)

        return counts
