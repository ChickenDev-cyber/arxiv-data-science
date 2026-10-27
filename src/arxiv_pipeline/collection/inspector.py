"""Read-only verification of one stopped run; implements ``RunReader``."""
from contextlib import contextmanager
import json
from pathlib import Path

from ..pipeline_contracts import FileDigest, VerifiedRun
from ..runner.scope import validate_scope_configuration
from ..runner.state import StateStore, options_from_config
from ..runner.writer import Publisher, digest
from ..validation.artifacts import safe_path
from ..validation.corpus import validate_corpus, validate_handoff

HANDOFF_FILES = ("scope.json", "accepted_results.jsonl", "validation.json", "quality.json")


class RunInspector:
    def __init__(self, open_store=StateStore.readonly):
        self.open_store = open_store

    @contextmanager
    def open(self, output_root: Path, run_id: str):
        root = Path(output_root)
        with self.open_store(root / "state.sqlite", run_id) as store:
            # A read transaction holds SQLite's shared lock, so no runner can
            # commit new results while the caller still uses this VerifiedRun.
            store.connection.execute("BEGIN")
            try:
                yield self._verify(root, run_id, store)
            finally:
                if store.connection.in_transaction:
                    store.connection.execute("ROLLBACK")

    def _verify(self, root, run_id, store):
        record = store.read_run_record()
        if record.status not in ("paused", "finished") or store.pending_publications():
            raise ValueError("Run is not stopped at a published checkpoint")
        if ([row[0] for row in store.connection.execute("PRAGMA quick_check")] != ["ok"]
                or list(store.connection.execute("PRAGMA foreign_key_check"))):
            raise ValueError("State database failed integrity checks")
        scope = store.read_scope()
        accepted = list(store.iter_results(accepted_only=True))
        validate_scope_configuration(scope, options_from_config(record.config))
        issues = validate_handoff(scope, accepted)
        context = {"run_id": run_id, "output_root": root, "is_mock": record.config["is_mock"]}
        publisher = Publisher(context, store)
        for task in store.tasks():
            issues += publisher.verify_task(task["task_key"])
        if issues:
            raise ValueError("Run handoff is invalid: " + issues[0]["message"])
        base = f"runs/{run_id}"
        exported = self._read_exports(root, base)
        if (exported["validation.json"] != {"valid": True, "issues": []}
                or exported["scope.json"] != scope or exported["accepted_results.jsonl"] != accepted):
            raise ValueError("Exported handoff is unavailable or differs from SQLite")
        files: list[FileDigest] = [{"path": a.path, "size_bytes": a.size_bytes, "sha256": a.sha256}
                                   for a in store.artifact_inventory()]
        for name in HANDOFF_FILES:
            path = safe_path(root, f"{base}/{name}")
            files.append({"path": f"{base}/{name}", "size_bytes": path.stat().st_size, "sha256": digest(path)})
        quality = validate_corpus(scope, accepted)
        return VerifiedRun(
            output_root=root, scope=scope, accepted_results=accepted,
            validation={"valid": True, "issues": []}, quality={"valid": not quality, "issues": quality},
            files=sorted(files, key=lambda f: f["path"]), run_state=record.status,
            config_fingerprint=record.fingerprint, recorded_output_root=record.config["output_root"])

    @staticmethod
    def _read_exports(root, base):
        exported = {}
        try:
            for name in HANDOFF_FILES:
                text = safe_path(root, f"{base}/{name}").read_text(encoding="utf-8")
                exported[name] = ([json.loads(line) for line in text.splitlines() if line.strip()]
                                  if name.endswith(".jsonl") else json.loads(text))
        except (OSError, ValueError) as exc:
            raise ValueError("Exported handoff is unavailable or differs from SQLite") from exc
        return exported
