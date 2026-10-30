"""Build one verified collection from snapshot archives."""
from pathlib import Path

from ..pipeline_contracts import CollectionManifest, RunReader, SnapshotReader
from ..validation.artifacts import safe_path
from ..validation.pipeline_contracts import validate_pipeline_document
from .assembler import CollectionAssembler
from .reader import COLLECTION_MANIFEST, CollectionDirectoryReader

SNAPSHOTS = "snapshots"


class CollectionService:
    """Unpack archives with the shared SnapshotReader, then assemble, verify and save.

    collection.json is written only after every snapshot and run has been verified,
    so a failed build leaves no collection that readers could pick up.
    """

    def __init__(self, snapshots: SnapshotReader, runs: RunReader, *,
                 assembler: CollectionAssembler | None = None):
        self.snapshots, self.runs = snapshots, runs
        self.assembler = assembler or CollectionAssembler()

    def create(self, archives: list[Path], collection_root: Path, collection_id: str) -> CollectionManifest:
        root = Path(collection_root)
        if safe_path(root, COLLECTION_MANIFEST).exists():
            raise ValueError(f"{root / COLLECTION_MANIFEST} already exists; use a new collection folder")
        folders = [self._unpack(Path(archive), root, index) for index, archive in enumerate(archives)]
        manifest = self.assembler.assemble(root, collection_id, folders)
        with CollectionDirectoryReader(self.runs).verify(root, manifest):
            pass
        CollectionAssembler.save(root, manifest)
        return manifest

    def _unpack(self, archive: Path, root: Path, index: int) -> str:
        """Unpack into a temporary folder, then name it after the verified snapshot ID."""
        staging = safe_path(root, f"{SNAPSHOTS}/.unpack-{index}")
        if staging.exists():
            raise ValueError(f"{staging} already exists; use a new collection folder")
        staging.parent.mkdir(parents=True, exist_ok=True)
        snapshot = self.snapshots.unpack(archive, staging)
        problems = validate_pipeline_document(snapshot, "snapshot")
        if problems:
            raise ValueError(f"Invalid snapshot manifest in {archive.name}: {problems[0]}")
        folder = f"{SNAPSHOTS}/{snapshot['snapshot_id']}"
        target = safe_path(root, folder)
        if target.exists():
            raise ValueError(f"Snapshot {snapshot['snapshot_id']} was given twice")
        staging.rename(target)
        return folder
