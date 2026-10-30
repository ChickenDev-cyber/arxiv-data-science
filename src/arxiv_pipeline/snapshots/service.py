"""Public snapshot operations; concrete readers and relocation are injected."""
from pathlib import Path
from typing import Callable

from ..pipeline_contracts import RunReader, SnapshotManifest, StateRelocator
from .common import ManifestValidator, SnapshotLimits, load_manifest_validator, utc_now


class SnapshotService:
    def __init__(self, reader: RunReader | None = None,
                 relocator: StateRelocator | None = None, *,
                 limits: SnapshotLimits | None = None,
                 validator: ManifestValidator | None = None,
                 clock: Callable[[], str] = utc_now):
        self.reader, self.relocator = reader, relocator
        self.limits = limits if limits is not None else SnapshotLimits()
        self.validator, self.clock = validator, clock

    def _validator(self) -> ManifestValidator:
        if self.validator is None:
            self.validator = load_manifest_validator()
        return self.validator

    def _archive(self):
        from .archive import SnapshotArchive
        return SnapshotArchive(self._validator(), limits=self.limits)

    def export(self, output_root: Path, run_id: str, destination: Path, *,
               snapshot_id: str, exporter_commit: str) -> Path:
        if self.reader is None:
            raise ValueError("Snapshot export requires a RunReader")
        from .exporter import SnapshotExporter
        return SnapshotExporter(
            self.reader, self._validator(), limits=self.limits, clock=self.clock
        ).export(Path(output_root), run_id, Path(destination),
                 snapshot_id=snapshot_id, exporter_commit=exporter_commit)

    def verify(self, archive: Path) -> SnapshotManifest:
        return self._archive().verify(Path(archive))

    def unpack(self, archive: Path, destination: Path) -> SnapshotManifest:
        return self._archive().unpack(Path(archive), Path(destination))

    def restore(self, archive: Path, destination: Path) -> SnapshotManifest:
        if self.reader is None or self.relocator is None:
            raise ValueError("Snapshot restore requires a RunReader and StateRelocator")
        from .restorer import SnapshotRestorer
        return SnapshotRestorer(
            self._archive(), self.reader, self.relocator, clock=self.clock
        ).restore(Path(archive), Path(destination))
