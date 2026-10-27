from arxiv_pipeline.examples import result

PAPER = {"owner": "trong", "arxiv_id": "2504.17523"}


def metadata_result(missing_v2_date=False):
    r = result("trong", "metadata", "2504.17523", {
        "arxiv_id": "2504.17523", "title": "Synthetic paper", "authors": ["A"],
        "submission_date": "2025-04-01", "revised_dates": [] if missing_v2_date else ["2025-04-02"],
        "venue": None, "versions": [{"version": 1, "submitted_at": "2025-04-01"},
                                      {"version": 2, "submitted_at": None if missing_v2_date else "2025-04-02"}],
        "versions_complete": not missing_v2_date,
    }, "partial" if missing_v2_date else "success")
    if missing_v2_date:
        r["missing_fields"] = ["/data/versions/1/submitted_at"]
    return r


def discovery_result(count=1):
    return result("trong", "discovery", None, {
        "papers": [{"owner": "trong", "arxiv_id": f"2504.{17523+i:05d}"} for i in range(count)],
        "unresolved_ids": [], "excluded_ids": [], "complete": False,
    }, "partial")


def references_result():
    r = result("trong", "references", "2504.17523", {
        "references": {}, "raw_references": [{"citedPaper": {"externalIds": {"DOI": "10.1/test"}}}],
        "complete": True,
    }, "empty")
    r["metrics"] = {"s2_reference_count": 1, "arxiv_reference_count": 0, "complete_reference_count": 0}
    return r


def source_result(root, run_id="mock-demo"):
    stage = root / "staging" / run_id / "trong/2504-17523/v1"
    stage.mkdir(parents=True)
    (stage / "main.tex").write_bytes(b"tex")
    (stage / "refs.bib").write_bytes(b"{}")
    r = result("trong", "sources", "2504.17523", {
        "source_kind": "tar", "source_dir": "raw/trong/2504-17523/v1",
        "files": [{"path": "main.tex", "size_bytes": 3}, {"path": "refs.bib", "size_bytes": 2}],
    }, version=1)
    r["run_id"] = run_id
    r["metrics"] = {"download_bytes": 10, "extracted_bytes_before_filter": 8,
                    "retained_source_bytes": 5, "removed_figure_bytes": 3, "removed_other_bytes": 0}
    return r


def date_checkpoint(arxiv_id="2504.17523"):
    """Complete operational record, with a hand-chosen arXiv date."""
    return {"format_version": 1, "namespace": "arxiv_dates", "key": f'["{arxiv_id}"]',
            "payload": {"arxiv_id": arxiv_id, "submission_date": "2025-04-24",
                        "source": "https://export.arxiv.org/api/query",
                        "fetched_at": "2026-10-27T00:00:00Z"},
            "updated_at": "2026-10-27T00:00:00Z"}


def make_v1_corpus(root):
    """Generate real accepted data, then rebuild the exact four-table v1 DB.

    Newer application code may create v2. Replaying only baseline tables makes
    this a genuine legacy fixture rather than relabelling a v2 database.
    """
    import sqlite3
    from contextlib import closing
    from arxiv_pipeline.runner.mock import make_mock_modules
    from arxiv_pipeline.runner.models import RunOptions
    from arxiv_pipeline.runner.service import run_pipeline

    options = RunOptions("legacy", root, ("trong",), True)
    run_pipeline(options, make_mock_modules(options))
    database = root / "state.sqlite"
    with closing(sqlite3.connect(database)) as connection:
        statements = [s for s in connection.iterdump()
                      if not s.startswith(('CREATE TABLE run_checkpoints',
                                           'INSERT INTO "run_checkpoints"'))]
    replacement = root / "legacy-copy.sqlite"
    with closing(sqlite3.connect(replacement)) as connection:
        connection.executescript("\n".join(statements))
        connection.execute("PRAGMA user_version=1")
    replacement.replace(database)
    return options
