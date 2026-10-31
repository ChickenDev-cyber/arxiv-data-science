"""Read-only verification of one stopped run; implements ``RunReader``."""
from contextlib import contextmanager
import json
from pathlib import Path

from ..contracts import ValidationReport
from ..pipeline_contracts import FileDigest, VerifiedRun
from ..runner.scope import validate_scope_configuration
from ..runner.state import OutputLock, StateStore, options_from_config
from ..runner.writer import Publisher, digest
from ..validation.artifacts import safe_path
from ..validation.corpus import validate_corpus, validate_handoff
from ..validation.results import _check

HANDOFF_FILES = ("scope.json", "accepted_results.jsonl", "validation.json", "quality.json")
# Files of the output root itself; the inventory must be enough to reopen a copy.
STATE_FILES = ("state.sqlite", ".runner.lock")


class RunInspector:
    def __init__(self, open_store=StateStore.readonly, lock=OutputLock):
        self.open_store, self.lock = open_store, lock

    @contextmanager
    def open(self, output_root: Path, run_id: str):
        root = Path(output_root)
        marker = root / ".runner.lock"
        # Check first: OutputLock would otherwise create the folder and the lock file.
        if not root.is_dir() or not marker.is_file() or marker.is_symlink():
            raise ValueError("Output root has no runner lock file: " + str(root))
        # The runner's exclusive output-root lock keeps runners and relocation out,
        # and the read transaction keeps SQLite unchanged while the caller reads.
        with self.lock(root), self.open_store(root / "state.sqlite", run_id) as store:
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
        base, events = f"runs/{run_id}", f"logs/{run_id}/events.jsonl"
        exported = {name: self._read(root, f"{base}/{name}") for name in HANDOFF_FILES}
        if (exported["validation.json"] != {"valid": True, "issues": []}
                or exported["scope.json"] != scope or exported["accepted_results.jsonl"] != accepted):
            raise ValueError("Exported handoff is unavailable or differs from SQLite")
        if self._read(root, events) != list(store.iter_results(accepted_only=False)):
            raise ValueError("Event log differs from the valid attempts in SQLite")
        quality = self._quality(exported["quality.json"], validate_corpus(scope, accepted))
        paths = [a.path for a in store.artifact_inventory()]
        paths += [f"{base}/{name}" for name in HANDOFF_FILES] + [events, *STATE_FILES]
        files: list[FileDigest] = []
        for relative in sorted(set(paths)):
            path = safe_path(root, relative)
            files.append({"path": relative, "size_bytes": path.stat().st_size, "sha256": digest(path)})
        return VerifiedRun(
            output_root=root, scope=scope, accepted_results=accepted,
            validation={"valid": True, "issues": []}, quality=quality, files=files,
            run_state=record.status, config_fingerprint=record.fingerprint,
            recorded_output_root=record.config["output_root"])

    @staticmethod
    def _quality(report, found):
        """quality.json may be invalid (work is left) but must be a well-formed, complete report."""
        if (_check(report, ValidationReport, "") or report["valid"] != (not report["issues"])
                or any(issue not in report["issues"] for issue in found)):
            raise ValueError("Quality report is malformed or misses issues found in SQLite")
        return report

    @staticmethod
    def _read(root, relative):
        try:
            text = safe_path(root, relative).read_text(encoding="utf-8")
            return ([json.loads(line) for line in text.splitlines() if line.strip()]
                    if relative.endswith(".jsonl") else json.loads(text))
        except (OSError, ValueError) as exc:
            raise ValueError(f"Run file {relative} is unavailable or not valid JSON") from exc
