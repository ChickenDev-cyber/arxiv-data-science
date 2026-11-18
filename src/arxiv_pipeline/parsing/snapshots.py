"""Immutable Drive snapshots; SQLite handles only touch closed local copies."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import uuid


def require_mount(record: dict) -> None:
    mount = record.get('drive_mount')
    if mount and not os.path.ismount(mount):
        raise RuntimeError('Drive mount lost; retain local output and restore mount before retry')


def sha256(path: Path) -> str:
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inspect_database(path: Path) -> dict:
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro&immutable=1', uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('Snapshot SQLite integrity failed')
        return {name: db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                for name, table in [('publications', 'publications_meta'),
                                    ('checkpoints', 'parsing_checkpoints')]}


def copy_once(source: Path, destination: Path) -> str:
    """Unique targets only. A failed transfer is never advertised as complete."""
    if destination.exists():
        raise FileExistsError(destination)
    expected = sha256(source)
    shutil.copyfile(source, destination)
    if sha256(destination) != expected:
        raise ValueError('Transfer checksum mismatch: '+str(destination))
    return expected


def publish_snapshot(source: Path, directory: Path, record: dict) -> dict:
    """Freeze matching local DB/hash, publish new generation and verify readback."""
    source, directory = Path(source), Path(directory)
    require_mount(record)
    checksum = source.with_suffix(source.suffix+'.sha256')
    expected = checksum.read_text().strip()
    # Parser can replace its local backup while this copy is made. Only a
    # matching frozen copy may proceed, otherwise the guardian retries later.
    with tempfile.TemporaryDirectory(prefix='m2-snapshot-') as tmp:
        local = Path(tmp)/'checkpoint.sqlite'
        shutil.copyfile(source, local)
        if sha256(local) != expected or checksum.read_text().strip() != expected:
            raise ValueError('Local checkpoint/hash changed or differ; retry after backup finishes')
        info = inspect_database(local)
        if info['publications'] != info['checkpoints']:
            raise ValueError('Snapshot publication/checkpoint coverage differs')
        generation = directory/f"snapshot-{info['publications']:05d}-{expected[:16]}-{uuid.uuid4().hex}"
        generation.mkdir(parents=True, exist_ok=False)
        target = generation/'checkpoint.sqlite'
        copy_once(local, target)
        readback = Path(tmp)/'readback.sqlite'
        shutil.copyfile(target, readback)
        if sha256(readback) != expected or inspect_database(readback) != info:
            raise ValueError('Independent Drive snapshot readback failed')
        (generation/'checkpoint.sqlite.sha256').write_text(expected+'\n')
        if (generation/'checkpoint.sqlite.sha256').read_text().strip() != expected:
            raise ValueError('Snapshot checksum sidecar readback failed')
        run = Path(tmp)/'run.json'
        run.write_text(json.dumps(record, indent=2)+'\n')
        run_sha = copy_once(run, generation/'run.json')
        receipt = {**info, 'sha256': expected, 'bytes': local.stat().st_size,
                   'directory': str(generation), 'run_sha256': run_sha}
        require_mount(record)
        # This is the only discovery marker. It is written after every check.
        marker = Path(tmp)/'complete.json'
        marker.write_text(json.dumps(receipt, indent=2)+'\n')
        copy_once(marker, generation/'complete.json')
    return receipt


def restore_snapshot(source: Path, destination: Path) -> dict:
    """Download a completed generation before any SQLite validation or restore."""
    source, destination = Path(source), Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    marker = source.parent/'complete.json'
    if not marker.is_file():
        raise ValueError('Resume requires a generation with complete.json; keep the original bundle for legacy checkpoints')
    receipt = json.loads(marker.read_text())
    expected = receipt['sha256']
    if source.with_suffix(source.suffix+'.sha256').read_text().strip() != expected:
        raise ValueError('Snapshot marker and checksum differ')
    # Stage outside Drive; no SQLite connection to the mounted source.
    with tempfile.TemporaryDirectory(prefix='m2-restore-') as tmp:
        local = Path(tmp)/'checkpoint.sqlite'
        shutil.copyfile(source, local)
        if sha256(local) != expected:
            raise ValueError('Resume snapshot readback checksum differs')
        info = inspect_database(local)
        if any(info[key] != receipt[key] for key in info):
            raise ValueError('Resume snapshot counts differ from completion marker')
        run = source.parent/'run.json'
        if sha256(run) != receipt['run_sha256']:
            raise ValueError('Resume run identity checksum differs')
        record = json.loads(run.read_text())
        require_mount(record)
        destination.parent.mkdir(parents=True, exist_ok=True)
        copy_once(local, destination)
        destination.with_suffix(destination.suffix+'.sha256').write_text(expected+'\n')
        (destination.parent/'run.json').write_text(json.dumps(record, indent=2)+'\n')
    return receipt
