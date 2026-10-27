"""Verify a stopped restored corpus before changing its saved output root."""

import json
from pathlib import Path
import re

from ..validation.corpus import validate_handoff
from ..validation.artifacts import safe_path
from .state import OutputLock, StateStore, options_from_config
from .scope import validate_scope_configuration
from .writer import Publisher


class RunStateRelocator:
    def relocate(self, output_root: Path, run_id: str, expected_old_root: str,
                 *, snapshot_id: str) -> None:
        root = Path(output_root)
        database = root / "state.sqlite"
        if (not isinstance(snapshot_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", snapshot_id)
                or not database.is_file() or database.is_symlink()):
            raise ValueError("Relocation requires a valid snapshot ID and an existing database")
        with OutputLock(root):
            # Verify read-only first: a rejected origin must not even migrate v1.
            with StateStore.readonly(database, run_id) as store:
                record = store.read_run_record()
                if record.config["output_root"] != expected_old_root:
                    raise ValueError("Saved output root differs from the snapshot origin")
                if record.status not in ("paused", "finished") or store.pending_publications():
                    raise ValueError("Run has no stopped, published checkpoint")
                # A task can remain 'running' after Ctrl+C or a stage switch.
                # The held kernel lock and stopped run establish inactivity;
                # accepted artifacts/journals, not that stale label, govern safety.
                if ([row[0] for row in store.connection.execute("PRAGMA quick_check")] != ["ok"]
                        or list(store.connection.execute("PRAGMA foreign_key_check"))):
                    raise ValueError("State database failed integrity checks")
                scope = store.read_scope()
                accepted = list(store.iter_results(accepted_only=True))
                if validate_handoff(scope, accepted):
                    raise ValueError("Restored handoff is inconsistent")
                validate_scope_configuration(scope, options_from_config(record.config))
                reports = f"runs/{run_id}"
                validation = json.loads(safe_path(root, reports + "/validation.json").read_text())
                exported_scope = json.loads(safe_path(root, reports + "/scope.json").read_text())
                exported_results = [json.loads(line) for line in safe_path(
                    root, reports + "/accepted_results.jsonl").read_text().splitlines() if line.strip()]
                if (validation != {"valid": True, "issues": []} or exported_scope != scope
                        or exported_results != accepted):
                    raise ValueError("Restored export is unavailable or differs from SQLite")
                context = {"run_id": run_id, "output_root": root, "is_mock": record.config["is_mock"]}
                publisher = Publisher(context, store)
                for task in store.tasks():
                    issues = publisher.verify_task(task["task_key"])
                    if issues:
                        raise ValueError(issues[0]["message"])
            with StateStore(database, run_id) as store:
                store.relocate_output_root(expected_old_root, root)
