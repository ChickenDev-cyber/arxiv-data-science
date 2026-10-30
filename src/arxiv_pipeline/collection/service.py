"""Build one verified collection from snapshot archives, and open it again."""
from pathlib import Path
from typing import ContextManager

from ..pipeline_contracts import CollectionManifest, RunReader, SnapshotReader, VerifiedCollection
from ..validation.artifacts import safe_path
from ..validation.pipeline_contracts import validate_pipeline_document
from .assembler import CollectionAssembler, utc_now
from .reader import CollectionDirectoryReader

SNAPSHOTS = "snapshots"


class CollectionService:
    """Unpack archives with the shared SnapshotReader, then assemble, verify and save.

    collection.json is written only after every snapshot and run has been verified,
    so a failed build leaves no collection that readers could pick up.
    """

    def __init__(self, snapshots: SnapshotReader, runs: RunReader, *, clock=utc_now):
        self.snapshots, self.runs = snapshots, runs
        self.assembler = CollectionAssembler(runs, clock=clock)

    def assemble(self, archives: list[Path], destination: Path, *, collection_id: str) -> CollectionManifest:
        """destination must be new or empty; each archive lands in snapshots/<snapshot_id>/."""
        root = Path(destination)
        if root.is_symlink() or (root.exists() and (not root.is_dir() or any(root.iterdir()))):
            raise ValueError(f"{root} already exists and is not empty; use a new collection folder")
        root.mkdir(parents=True, exist_ok=True)
        folders = [self._unpack(Path(archive), root, index) for index, archive in enumerate(archives)]
        return self.assembler.assemble(root, collection_id, folders)

    def open(self, collection_root: Path) -> ContextManager[VerifiedCollection]:
        return CollectionDirectoryReader(self.runs).open(Path(collection_root))

    def create(self, archives: list[Path], collection_root: Path, collection_id: str) -> CollectionManifest:
        """Kept for earlier callers; same as assemble."""
        return self.assemble(archives, collection_root, collection_id=collection_id)

    def _unpack(self, archive: Path, root: Path, index: int) -> str:
        """Unpack into a temporary folder, then name it after the verified snapshot ID."""
        staging = safe_path(root, f"{SNAPSHOTS}/.unpack-{index}")
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
