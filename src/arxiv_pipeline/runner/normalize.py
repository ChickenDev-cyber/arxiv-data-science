"""Normalize run handoffs and attempts to Schema 1.2 compliance."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

from ..contracts import SCHEMA_VERSION
from ..json_utils import canonical
from ..runner.exports import Exporter
from ..runner.state import OutputLock, StateStore
from ..runner.writer import Publisher
from ..validation.results import pointer

MEMBERS = [
    ("trong", "m1-colab-trong-01", Path("data/m1-colab-trong-01/corpus")),
    ("khang", "m1-colab-khang-01", Path("data/m1-colab-khang-01/corpus")),
    ("hoang", "m1-colab-hoang-01", Path("data/m1-colab-hoang-01/corpus")),
    ("huynh-nhi", "m1-colab-huynh-nhi-01", Path("data/m1-colab-huynh-nhi-01/corpus")),
    ("man-nhi", "m1-colab-man-nhi-01", Path("data/m1-colab-man-nhi-01/corpus")),
]


def normalize_run(root: Path, run_id: str) -> None:
    root = Path(root).resolve()
    db_path = root / "state.sqlite"
    if not db_path.is_file():
        raise FileNotFoundError(f"State database not found: {db_path}")

    with OutputLock(root):
        con = sqlite3.connect(db_path, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=DELETE")
        con.execute("PRAGMA foreign_keys=ON")

        # 1. Update scope in runs table if needed
        row = con.execute("SELECT scope_json FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            raise ValueError(f"Run {run_id} not found in {db_path}")
        scope_data = json.loads(row[0])
        if not scope_data["scope"].get("versions_complete", False):
            scope_data["scope"]["versions_complete"] = True
            con.execute("UPDATE runs SET scope_json=? WHERE run_id=?", (canonical(scope_data), run_id))
            print(f"[{run_id}] Updated scope.versions_complete = True")

        # 2. Update attempts for references
        attempts_updated = 0
        cur = con.execute(
            "SELECT attempt_id, result_json FROM attempts WHERE run_id=? AND task_key LIKE '%\"references\"%' AND result_valid=1",
            (run_id,)
        )
        for att in cur.fetchall():
            att_id = att["attempt_id"]
            res = json.loads(att["result_json"])
            changed = False

            # Check 0 references with status == 'success'
            data = res.get("data") or {}
            refs = data.get("references") or {}
            if res.get("status") == "success" and not refs:
                res["status"] = "empty"
                res["missing_fields"] = []
                changed = True

            # Check unescaped pointers in missing_fields
            mf = res.get("missing_fields") or []
            new_mf = []
            mf_changed = False
            for p in mf:
                parts = p.split("/")
                if len(parts) > 5 and parts[1] == "data" and parts[2] == "references":
                    key = "/".join(parts[3:-1])
                    field = parts[-1]
                    new_mf.append(f"/data/references/{pointer(key)}/{field}")
                    mf_changed = True
                else:
                    new_mf.append(p)
            if mf_changed:
                res["missing_fields"] = new_mf
                changed = True

            if changed:
                con.execute(
                    "UPDATE attempts SET result_json=?, issues_json='[]' WHERE attempt_id=?",
                    (canonical(res), att_id)
                )
                attempts_updated += 1

        print(f"[{run_id}] Updated {attempts_updated} reference attempts in SQLite")
        con.execute("PRAGMA optimize")
        con.close()

        # 3. Regenerate exports using Exporter and Publisher
        store = StateStore(db_path, run_id, read_only=False)
        try:
            rec = store.read_run_record()
            context = {"run_id": run_id, "output_root": root, "is_mock": rec.config["is_mock"]}
            publisher = Publisher(context, store)
            exporter = Exporter(context, store, publisher)
            exporter.checkpoint()
            exporter.rebuild_events()
            print(f"[{run_id}] Rebuilt checkpoint handoff (scope.json, accepted_results.jsonl, validation.json, quality.json) and events.jsonl")
        finally:
            store.connection.close()


def main():
    parser = argparse.ArgumentParser(description="Normalize run results and regenerate Schema 1.2 handoff files.")
    parser.add_argument("--all", action="store_true", help="Normalize all 5 team members' corpora.")
    parser.add_argument("--output-root", type=str, help="Corpus output root directory.")
    parser.add_argument("--run-id", type=str, help="Run ID.")
    args = parser.parse_args()

    if args.all:
        for owner, run_id, path in MEMBERS:
            print(f"\n=== Normalizing {owner.upper()} ({run_id}) ===")
            normalize_run(path, run_id)
        print("\nAll corpora normalized successfully.")
    elif args.output_root and args.run_id:
        normalize_run(Path(args.output_root), args.run_id)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
