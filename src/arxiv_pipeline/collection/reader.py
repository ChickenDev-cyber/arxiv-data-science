"""Open an assembled collection and verify every member; implements ``CollectionReader``."""
from contextlib import ExitStack, contextmanager
import hashlib
import json
from pathlib import Path
import re

from ..pipeline_contracts import RunReader, VerifiedCollection
from ..validation.artifacts import safe_path
from ..validation.pipeline_contracts import validate_pipeline_document

COLLECTION_MANIFEST = "collection.json"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def read_document(root: Path, relative: str, kind: str) -> dict:
    """Load a JSON document inside root and reject it unless the validator accepts it."""
    try:
        document = json.loads(safe_path(root, relative).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read {kind} manifest {relative}") from exc
    problems = validate_pipeline_document(document, kind)
    if problems:
        raise ValueError(f"Invalid {kind} manifest {relative}: {problems[0]}")
    return document


def verify_files(root: Path, files) -> None:
    for f in files:
        path = safe_path(root, f["path"])
        if not path.is_file() or path.stat().st_size != f["size_bytes"] or sha256_file(path) != f["sha256"]:
            raise ValueError("Snapshot file missing or changed: " + f["path"])


def run_owners(run) -> list[str]:
    return sorted(c["owner"] for c in run.scope["scope"]["ranges"])


def provenance_of(results) -> tuple[list[str], int]:
    """Distinct producer commits of accepted Results, and how many Results have none.

    Counted as the snapshot exporter does: only a full 40-hex commit is a known producer.
    """
    commits = [(r.get("provenance") or {}).get("producer_commit") for r in results]
    known = [c for c in commits if isinstance(c, str) and re.fullmatch(r"[0-9a-f]{40}", c)]
    return sorted(set(known)), len(commits) - len(known)


def check_snapshot_run(snapshot, run) -> None:
    """The manifest must describe exactly this run: identity, origin, provenance and inventory."""
    commits, unknown = provenance_of(run.accepted_results)
    problems = [name for name, ok in (
        ("run_id", run.scope["run_id"] == snapshot["run_id"]),
        ("is_mock", run.scope["is_mock"] == snapshot["is_mock"]),
        ("owners", run_owners(run) == sorted(snapshot["owners"])),
        ("config_fingerprint", run.config_fingerprint == snapshot["config_fingerprint"]),
        ("origin_output_root", run.recorded_output_root == snapshot["origin_output_root"]),
        ("producer_commits", sorted(snapshot["producer_commits"]) == commits),
        ("unknown_provenance_tasks", snapshot["unknown_provenance_tasks"] == unknown),
        ("files", sorted(run.files, key=lambda f: f["path"])
         == sorted(snapshot["files"], key=lambda f: f["path"]))) if not ok]
    if problems:
        raise ValueError(f"Snapshot {snapshot['snapshot_id']} differs from its verified run: "
                         + ", ".join(problems))


class CollectionDirectoryReader:
    def __init__(self, runs: RunReader):
        self.runs = runs

    def open(self, collection_root: Path):
        root = Path(collection_root)
        return self.verify(root, read_document(root, COLLECTION_MANIFEST, "collection"))

    @contextmanager
    def verify(self, root: Path, manifest):
        """Check every member of a collection manifest, saved or not yet saved."""
        if validate_pipeline_document(manifest, "collection"):
            raise ValueError("Invalid collection manifest")
        with ExitStack() as stack:
            verified = []
            for member in manifest["members"]:
                snapshot = read_document(root, member["manifest_path"], "snapshot")
                if any(snapshot[k] != member[k] for k in ("snapshot_id", "run_id", "owners", "exporter_commit")):
                    raise ValueError("Collection member differs from its snapshot: " + member["snapshot_id"])
                if snapshot["is_mock"] != manifest["is_mock"]:
                    raise ValueError("Collection mixes mock and real data: " + member["snapshot_id"])
                # The validator already tied root to <snapshot>/corpus next to the manifest.
                corpus = safe_path(root, member["root"])
                verify_files(corpus, snapshot["files"])
                run = stack.enter_context(self.runs.open(corpus, member["run_id"]))
                check_snapshot_run(snapshot, run)
                verified.append(run)
            yield VerifiedCollection(manifest=manifest, runs=verified)
