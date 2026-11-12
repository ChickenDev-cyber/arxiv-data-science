"""Relational storage engine for parsed elements, hierarchy, and BibTeX tables."""

import json
import hashlib
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
                bibtex_id TEXT PRIMARY KEY,
                arxiv_id TEXT NOT NULL,
                citation_key TEXT NOT NULL,
                entry_type TEXT NOT NULL,
                title TEXT,
                authors TEXT,
                year INTEGER,
                doi TEXT,
                journal TEXT,
                url TEXT
            );
        """)
        bibtex_info = cursor.execute("PRAGMA table_info(bibtex)").fetchall()
        bibtex_columns = {row[1] for row in bibtex_info}
        if "bibtex_id" not in bibtex_columns:
            cursor.execute("ALTER TABLE bibtex RENAME TO bibtex_legacy;")
            cursor.execute("""
                CREATE TABLE bibtex (
                    bibtex_id TEXT PRIMARY KEY,
                    arxiv_id TEXT NOT NULL,
                    citation_key TEXT NOT NULL,
                    entry_type TEXT NOT NULL,
                    title TEXT,
                    authors TEXT,
                    year INTEGER,
                    doi TEXT,
                    journal TEXT,
                    url TEXT
                );
            """)
            legacy_rows = cursor.execute("SELECT arxiv_id, citation_key, entry_type, title, authors, year, doi, journal, url FROM bibtex_legacy").fetchall()
            for row in legacy_rows:
                stable = hashlib.sha256("\0".join(str(value or "") for value in row).encode("utf-8")).hexdigest()
                cursor.execute("INSERT OR IGNORE INTO bibtex VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (stable, *tuple(row)))
            cursor.execute("DROP TABLE bibtex_legacy;")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_bibtex_arxiv ON bibtex(arxiv_id);")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS publications_meta (
                arxiv_id TEXT PRIMARY KEY,
                version_count INTEGER NOT NULL,
                empty_versions TEXT NOT NULL,
                raw_element_count INTEGER NOT NULL DEFAULT 0,
                raw_bibtex_count INTEGER NOT NULL DEFAULT 0,
                bib_source_kind TEXT NOT NULL DEFAULT 'none'
            );
        """)
        existing_cols = {
            row[1] for row in cursor.execute("PRAGMA table_info(publications_meta)").fetchall()
        }
        if "raw_element_count" not in existing_cols:
            cursor.execute("ALTER TABLE publications_meta ADD COLUMN raw_element_count INTEGER NOT NULL DEFAULT 0;")
        if "raw_bibtex_count" not in existing_cols:
            cursor.execute("ALTER TABLE publications_meta ADD COLUMN raw_bibtex_count INTEGER NOT NULL DEFAULT 0;")
        if "bib_source_kind" not in existing_cols:
            cursor.execute("ALTER TABLE publications_meta ADD COLUMN bib_source_kind TEXT NOT NULL DEFAULT 'none';")
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
        rows = []
        for record in records:
            values = (
                record["arxiv_id"], record["citation_key"], record["entry_type"],
                record.get("title", ""), json.dumps(record.get("authors", []), ensure_ascii=False),
                record.get("year"), record.get("doi", ""), record.get("journal", ""), record.get("url", ""),
            )
            stable = hashlib.sha256("\0".join(str(value or "") for value in values).encode("utf-8")).hexdigest()
            rows.append((stable, *values))
        cursor.executemany(
            """INSERT OR REPLACE INTO bibtex
               (bibtex_id, arxiv_id, citation_key, entry_type, title, authors, year, doi, journal, url)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
            rows,
        )

    def save_publication(self, result: PublicationParsingResult) -> None:
        self.insert_elements(result["elements"])
        self.insert_hierarchy(result["hierarchy"])
        self.insert_bibtex(result["bibtex"])

        raw_el_count = result.get("raw_element_count", len(result["elements"]))
        raw_bib_count = result.get("raw_bibtex_count", len(result["bibtex"]))
        bib_source_kind = result.get("bib_source_kind", "none")

        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO publications_meta
                (arxiv_id, version_count, empty_versions, raw_element_count, raw_bibtex_count, bib_source_kind)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            (
                result["arxiv_id"],
                result["version_count"],
                json.dumps(result["empty_versions"]),
                raw_el_count,
                raw_bib_count,
                bib_source_kind,
            ),
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
            """
            SELECT arxiv_id, version_count, empty_versions, raw_element_count, raw_bibtex_count, bib_source_kind
            FROM publications_meta WHERE arxiv_id = ?
            """,
            (arxiv_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        d = dict(row)
        d["empty_versions"] = json.loads(d["empty_versions"]) if d["empty_versions"] else []
        return d

    def get_corpus_statistics(self) -> Dict[str, Any]:
        """Aggregate corpus-wide statistics for Jupyter Book reporting."""
        cursor = self.conn.cursor()
        meta_rows = cursor.execute(
            "SELECT version_count, empty_versions, raw_element_count, raw_bibtex_count, bib_source_kind FROM publications_meta"
        ).fetchall()

        total_versions = 0
        empty_versions_count = 0
        raw_elements = 0
        raw_bibtex = 0
        bib_source_counts: Dict[str, int] = {}

        for row in meta_rows:
            total_versions += row["version_count"]
            empties = json.loads(row["empty_versions"]) if row["empty_versions"] else []
            empty_versions_count += len(empties)
            raw_elements += row["raw_element_count"]
            raw_bibtex += row["raw_bibtex_count"]
            src_kind = row["bib_source_kind"] or "none"
            bib_source_counts[src_kind] = bib_source_counts.get(src_kind, 0) + 1

        elements_after = cursor.execute("SELECT COUNT(*) FROM elements").fetchone()[0]
        hierarchy_edges = cursor.execute("SELECT COUNT(*) FROM hierarchy").fetchone()[0]
        bibtex_after = cursor.execute("SELECT COUNT(*) FROM bibtex").fetchone()[0]

        return {
            "publications_count": len(meta_rows),
            "total_versions": total_versions,
            "empty_versions_count": empty_versions_count,
            "parsed_versions_count": max(0, total_versions - empty_versions_count),
            "elements_before_dedup": raw_elements,
            "elements_after_dedup": elements_after,
            "hierarchy_edges_count": hierarchy_edges,
            "bibtex_before_dedup": raw_bibtex,
            "bibtex_after_dedup": bibtex_after,
            "bib_source_counts": bib_source_counts,
        }

    def _stream_table_to_parquet(
        self,
        sql: str,
        table_dir: Path,
        build_table_fn: Any,
        pq_mod: Any,
        shard_size: int,
    ) -> int:
        """Stream SQL query results in chunks into sharded Parquet files."""
        cursor = self.conn.cursor()
        cursor.execute(sql)
        total_rows = 0
        shard_idx = 0

        while True:
            batch = cursor.fetchmany(shard_size)
            if not batch:
                break
            table_dir.mkdir(parents=True, exist_ok=True)
            arrow_table = build_table_fn(batch)
            pq_mod.write_table(
                arrow_table,
                table_dir / f"part-{shard_idx:05d}.parquet",
                compression="snappy",
            )
            total_rows += len(batch)
            shard_idx += 1

        return total_rows

    def export_to_parquet(
        self,
        output_dir: Path | str,
        shard_size: int = 250_000,
    ) -> Dict[str, int]:
        """Export SQLite tables to Hugging Face-ready sharded Parquet files."""
        if shard_size <= 0:
            raise ValueError("shard_size must be a positive integer.")

        try:
            import pyarrow as pa
            import pyarrow.parquet as pq

            if pa is None or pq is None:
                raise ImportError()
        except (ImportError, AttributeError):
            raise RuntimeError("pyarrow is required for Parquet export. Run pip install pyarrow.")

        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        def build_elements(rows: List[Any]) -> Any:
            return pa.Table.from_arrays(
                [
                    pa.array([r[0] for r in rows], type=pa.string()),
                    pa.array([r[1] for r in rows], type=pa.string()),
                    pa.array([r[2] for r in rows], type=pa.string()),
                    pa.array([r[3] for r in rows], type=pa.string()),
                ],
                names=["element_id", "arxiv_id", "kind", "content"],
            )

        def build_hierarchy(rows: List[Any]) -> Any:
            return pa.Table.from_arrays(
                [
                    pa.array([r[0] for r in rows], type=pa.string()),
                    pa.array([r[1] for r in rows], type=pa.int32()),
                    pa.array([r[2] for r in rows], type=pa.string()),
                    pa.array([r[3] for r in rows], type=pa.string()),
                ],
                names=["arxiv_id", "version", "element_id", "parent_id"],
            )

        def build_bibtex(rows: List[Any]) -> Any:
            parsed_authors = [json.loads(r[4]) if r[4] else [] for r in rows]
            return pa.Table.from_arrays(
                [
                    pa.array([r[0] for r in rows], type=pa.string()),
                    pa.array([r[1] for r in rows], type=pa.string()),
                    pa.array([r[2] for r in rows], type=pa.string()),
                    pa.array([r[3] for r in rows], type=pa.string()),
                    pa.array(parsed_authors, type=pa.list_(pa.string())),
                    pa.array([r[5] for r in rows], type=pa.int32()),
                    pa.array([r[6] for r in rows], type=pa.string()),
                    pa.array([r[7] for r in rows], type=pa.string()),
                    pa.array([r[8] for r in rows], type=pa.string()),
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

        return {
            "elements": self._stream_table_to_parquet(
                "SELECT element_id, arxiv_id, kind, content FROM elements",
                out_path / "elements",
                build_elements,
                pq,
                shard_size,
            ),
            "hierarchy": self._stream_table_to_parquet(
                "SELECT arxiv_id, version, element_id, parent_id FROM hierarchy",
                out_path / "hierarchy",
                build_hierarchy,
                pq,
                shard_size,
            ),
            "bibtex": self._stream_table_to_parquet(
                "SELECT arxiv_id, citation_key, entry_type, title, authors, year, doi, journal, url FROM bibtex",
                out_path / "bibtex",
                build_bibtex,
                pq,
                shard_size,
            ),
        }
