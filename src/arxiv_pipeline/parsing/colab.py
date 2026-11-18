"""Staging and durable handoff shared by the M2 notebook and CLI smoke run.

Setup and verification run outside the parser benchmark timer.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
import subprocess
import time
import uuid
import zipfile

from .benchmark import source_fingerprint
from .runner import discover_paper_dirs


def sha256(path: Path) -> str:
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def copy_verified(source: Path, destination: Path) -> str:
    """Publish only after destination readback agrees; keep old data on failure."""
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve():
        raise ValueError('Source and destination must differ')
    expected = sha256(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + destination.name, suffix='.part', dir=destination.parent)
    os.close(fd)
    temp = Path(name)
    try:
        shutil.copyfile(source, temp)
        if sha256(temp) != expected:
            raise ValueError('Destination checksum mismatch: ' + str(destination))
        os.replace(temp, destination)
        if sha256(destination) != expected:
            raise ValueError('Published destination checksum mismatch: ' + str(destination))
        checksum = destination.with_suffix(destination.suffix + '.sha256')
        fd, name = tempfile.mkstemp(prefix='.sha256-', dir=destination.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(expected + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, checksum)
        finally:
            Path(name).unlink(missing_ok=True)
    finally:
        temp.unlink(missing_ok=True)
    return expected


def resolve_team_root(mount: Path, folder_id: str, explicit: str = '') -> Path:
    """Resolve a shared folder; never silently substitute a personal directory."""
    mount = Path(mount)
    shortcut = mount / '.shortcut-targets-by-id' / folder_id
    if explicit:
        candidate = Path(explicit).resolve()
        if not candidate.is_relative_to(mount.resolve()):
            raise ValueError('Team directory must be inside mounted Drive')
        if '.shortcut-targets-by-id' in candidate.parts and not candidate.is_relative_to(shortcut.resolve()):
            raise ValueError('Shortcut belongs to another folder ID')
        if candidate.is_dir():
            return candidate
    for candidate in [shortcut / 'arXiv-data', shortcut, mount / 'MyDrive/arXiv-data']:
        if candidate.is_dir():
            # A shortcut may expose the shared folder as one child directory.
            if candidate == shortcut and not (candidate / 'Full-data').is_dir():
                children = [p for p in candidate.iterdir() if p.is_dir()]
                if len(children) == 1:
                    return children[0]
            return candidate
    raise FileNotFoundError('Add the team folder shortcut to MyDrive: '
                            'https://drive.google.com/drive/folders/' + folder_id)


def stage_corpus(archive: Path, destination: Path, owner: str, *, expected_count: int = 5000) -> dict:
    """Extract into a new directory, including empty versions, and inventory IDs."""
    archive, destination = Path(archive), Path(destination)
    if destination.exists():
        raise FileExistsError('Keep existing input; choose a new local attempt directory')
    digest = sha256(archive)
    prefix = f'm1-colab-{owner}-01/corpus/raw/{owner}/'
    with zipfile.ZipFile(archive) as z:
        members = []
        for member in z.infolist():
            if not member.filename.startswith(prefix):
                continue
            relative = member.filename[len(prefix):]
            if not relative:
                continue
            path = Path(relative)
            if path.is_absolute() or '..' in path.parts or '\\' in relative:
                raise ValueError('Unsafe archive path: ' + relative)
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Archive symlinks are unsupported')
            members.append((member, relative))
        if not members:
            raise ValueError('Archive does not contain the selected owner prefix')
        destination.mkdir(parents=True, exist_ok=False)
        for member, relative in members:
            target = destination / relative
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(member) as incoming, target.open('wb') as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
    papers = discover_paper_dirs(destination)
    if len(papers) != expected_count:
        raise ValueError(f'Expected {expected_count} publications, found {len(papers)}; retain staging for inspection')
    ids, versions = set(), 0
    for paper in papers:
        metadata = json.loads((paper / 'metadata.json').read_text())
        aid = metadata['arxiv_id']
        if aid in ids:
            raise ValueError('Duplicate publication ID: ' + aid)
        ids.add(aid)
        versions += len({int(v['version']) for v in metadata['versions']})
    result = {'archive_sha256': digest, 'owner': owner, 'publications': len(ids),
              'declared_versions': versions, 'arxiv_ids': sorted(ids)}
    (destination.parent / 'input.json').write_text(json.dumps(result, indent=2))
    return result


def prepare_run(corpus: Path, output: Path, drive_output: Path, *, owner: str, run_id: str,
                source_sha: str, archive_sha: str, resume_from: Path | None = None,
                backup_every: int = 1000, drive_mount: Path | None = None) -> dict:
    """Reserve a unique run; leave creation of the output directory to benchmark."""
    corpus, output, drive_output = Path(corpus), Path(output), Path(drive_output)
    from .snapshots import require_mount
    require_mount({'drive_mount': str(drive_mount) if drive_mount else None})
    if output.exists() or drive_output.exists():
        raise FileExistsError('Attempt exists; keep it and select a new RUN_ID for fresh/resume')
    if not corpus.is_dir() or backup_every < 1:
        raise ValueError('Invalid corpus or backup interval')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', run_id):
        raise ValueError('RUN_ID must contain only letters, numbers, underscores or hyphens')
    if resume_from:
        resume_from = Path(resume_from).resolve()
        old = json.loads((resume_from.parent / 'run.json').read_text())
        identity = {'owner': owner, 'source_sha256': source_sha, 'archive_sha256': archive_sha}
        if any(old.get(k) != v for k, v in identity.items()):
            raise ValueError('Resume owner, input archive or source code differs from the prior run')
        if old.get('python') not in (None, sys.version):
            raise ValueError('Resume Python differs; restore the original runtime environment')
        checksum = resume_from.with_suffix(resume_from.suffix + '.sha256')
        if not checksum.is_file() or sha256(resume_from) != checksum.read_text().strip():
            raise ValueError('Resume requires a complete snapshot/checksum pair')
    local_resume = None
    if resume_from:
        from .snapshots import restore_snapshot
        local_resume = output.parent/'restore/checkpoint.sqlite'
        restore_snapshot(resume_from, local_resume)
    drive_output.mkdir(parents=True, exist_ok=False)
    record = {'owner': owner, 'run_id': run_id, 'source_sha256': source_sha,
              'archive_sha256': archive_sha, 'python': sys.version,
              'mode': 'resume' if resume_from else 'fresh',
              'resume_from': str(resume_from) if resume_from else None,
              'drive_mount': str(drive_mount) if drive_mount else None}
    (drive_output / 'run.json').write_text(json.dumps(record, indent=2))
    backup = output.parent / 'checkpoints/checkpoint.sqlite'
    command = [sys.executable, '-m', 'arxiv_pipeline.parsing.benchmark',
               '--corpus', str(corpus), '--output-dir', str(output), '--backend', 'rule',
               '--parquet', '--single-parquet', '--checkpoint',
               '--backup-db', str(backup), '--backup-every', str(backup_every)]
    if resume_from:
        command.extend(['--resume-from', str(local_resume)])
    return {'command': command, 'backup_db': str(backup), 'record': record,
            'drive_output': str(drive_output), 'output': str(output)}


def verify_results(corpus: Path, output: Path, input_receipt: dict, expected_source: str) -> dict:
    """Check coverage, relational integrity, table schemas/counts, and snapshot."""
    output = Path(output)
    bench = json.loads((output / 'benchmark.json').read_text())
    if bench['status'] != 'complete' or bench['source_sha256'] != expected_source:
        raise ValueError('Benchmark incomplete or ran a different source revision')
    from pyarrow.parquet import ParquetFile
    stats = json.loads((output / 'statistics/publications.json').read_text())
    if {p['arxiv_id'] for p in stats} != set(input_receipt['arxiv_ids']):
        raise ValueError('Publication identities differ from input')
    if any(not p['execution_success'] or p['unaccounted_versions'] or p['unexpected_versions'] for p in stats):
        raise ValueError('Failed publication or version coverage gap')
    if sum(len(p['declared_versions']) for p in stats) != input_receipt['declared_versions']:
        raise ValueError('Version denominator changed')
    report = {'status': 'complete', 'publications': len(stats), 'table_rows': {}}
    with closing(sqlite3.connect((output/'parsing.sqlite').resolve().as_uri()+'?mode=ro', uri=True)) as conn:
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('SQLite integrity failure')
        dangling = conn.execute('''SELECT COUNT(*) FROM hierarchy h
            LEFT JOIN elements e ON e.element_id=h.element_id
            LEFT JOIN elements p ON p.element_id=h.parent_id
            WHERE e.element_id IS NULL OR p.element_id IS NULL
               OR e.arxiv_id!=h.arxiv_id OR p.arxiv_id!=h.arxiv_id OR h.element_id=h.parent_id''').fetchone()[0]
        if dangling:
            raise ValueError('Dangling, cross-publication or self edges')
        for table in ['elements', 'hierarchy', 'bibtex']:
            count = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
            if ParquetFile(output/'parquet'/f'{table}.parquet').metadata.num_rows != count:
                raise ValueError('Parquet row count mismatch: ' + table)
            report['table_rows'][table] = count
    report['limits'] = ['Operational coverage and relational checks are not semantic accuracy.']
    (output/'verification.json').write_text(json.dumps(report, indent=2))
    return report


def run_prepared(run: dict, *, env: dict | None = None, poll_seconds: float = 5.0) -> dict:
    """Supervise measured child and transport its closed local checkpoints.

    Guardian RSS is outside the benchmark process tree; concurrent transport may
    contend for CPU/IO. The benchmark still includes local SQLite backup cost.
    """
    from .snapshots import publish_snapshot
    if poll_seconds <= 0:
        raise ValueError('poll_seconds must be positive')
    output, drive = Path(run['output']), Path(run['drive_output'])
    backup = Path(run['backup_db'])
    state = {'status': 'running', 'snapshots': [], 'transfer_errors': [],
             'measurement_limits': [
                 'Drive guardian RSS is outside benchmark process tree.',
                 'Concurrent transport may contend for CPU/IO.',
                 'Readback verifies the current Drive mount, not cross-VM persistence.']}
    last_digest = None

    def publish_available():
        nonlocal last_digest
        checksum = backup.with_suffix(backup.suffix+'.sha256')
        if not backup.is_file() or not checksum.is_file():
            return
        digest = checksum.read_text().strip()
        if digest == last_digest:
            return
        receipt = publish_snapshot(backup, drive/'snapshots', run['record'])
        state['snapshots'].append(receipt)
        print(f"Verified Drive snapshot: {receipt['publications']} publications → {receipt['directory']}", flush=True)
        status = output/'snapshot-status.json'
        status.write_text(json.dumps(state, indent=2)+'\n')
        last_digest = receipt['sha256']

    process = subprocess.Popen(run['command'], env=env)
    try:
        while process.poll() is None:
            try:
                publish_available()
            except Exception as exc:
                state['transfer_errors'].append(str(exc))
            # Only wait on our child; Ctrl+C propagates to benchmark normally.
            try:
                process.wait(timeout=poll_seconds)
            except subprocess.TimeoutExpired:
                pass
    except KeyboardInterrupt:
        import signal
        process.send_signal(signal.SIGINT)
        process.wait()
    finally:
        # Never copy an active SQLite primary; the backup pair is closed.
        try:
            publish_available()
            checksum = backup.with_suffix(backup.suffix+'.sha256')
            if not last_digest or checksum.read_text().strip() != last_digest:
                raise ValueError('No verified final snapshot')
            state['status'] = 'complete' if process.returncode == 0 else 'incomplete'
        except Exception as exc:
            state['status'] = 'incomplete'
            state['transfer_errors'].append(str(exc))
        state['returncode'] = process.returncode
        output.mkdir(parents=True, exist_ok=True)
        (output/'snapshot-status.json').write_text(json.dumps(state, indent=2)+'\n')
    return state


def sync_results(output: Path, destination: Path) -> dict:
    """Salvage every available artifact to a new generation before validation.

    This receipt only confirms transport. finalize_run alone may write handoff.
    Call only after the benchmark process exits and closes its database.
    """
    output, destination = Path(output), Path(destination)
    from .snapshots import require_mount
    record_path = destination/'run.json'
    record = json.loads(record_path.read_text()) if record_path.is_file() else {}
    require_mount(record)
    generation = destination/'artifacts'/('result-'+uuid.uuid4().hex)
    generation.mkdir(parents=True, exist_ok=False)
    hashes = {}
    errors = {}
    for source in sorted(output.rglob('*')):
        if not source.is_file() or source.name.startswith('.') or source.suffix in {'.part', '.sha256'}:
            continue
        name = source.relative_to(output).as_posix()
        if name in {'verification.json', 'snapshot-status.json', 'validation-error.json', 'handoff.json'}:
            name = 'before-validation/'+name
        try:
            require_mount(record)
            hashes[name] = copy_verified(source, generation/name)
        except Exception as exc:
            errors[name] = str(exc)
    require_mount(record)
    receipt = {'status': 'artifacts_saved' if not errors else 'partial',
               'directory': str(generation), 'sha256': hashes, 'errors': errors}
    (generation/'artifacts.json').write_text(json.dumps(receipt, indent=2)+'\n')
    return receipt


def finalize_run(corpus: Path, output: Path, destination: Path,
                 input_receipt: dict, expected_source: str) -> dict:
    """Salvage first; mark handoff only after coverage and final snapshot checks."""
    output, destination = Path(output), Path(destination)
    if (destination/'handoff.json').exists():
        raise FileExistsError('Handoff already complete; retain it and use a new run')
    saved = sync_results(output, destination)
    generation = Path(saved['directory'])
    try:
        if saved['errors']:
            raise ValueError('Some artifacts failed transfer: '+str(saved['errors']))
        verification = verify_results(corpus, output, input_receipt, expected_source)
        from .snapshots import publish_snapshot, restore_snapshot, require_mount
        snapshots = json.loads((output/'snapshot-status.json').read_text())
        record = json.loads((destination/'run.json').read_text())
        require_mount(record)
        if record['source_sha256'] != expected_source:
            raise ValueError('Retry snapshot source identity differs')

        def check_snapshot(candidate):
            with tempfile.TemporaryDirectory(prefix='m2-check-snapshot-') as tmp:
                checked = restore_snapshot(Path(candidate['directory'])/'checkpoint.sqlite',
                                           Path(tmp)/'snapshot.sqlite')
                if checked['sha256'] != candidate['sha256']:
                    raise ValueError('Final snapshot differs from guardian receipt')
                if checked['publications'] != verification['publications'] or checked['checkpoints'] != verification['publications']:
                    raise ValueError('Final Drive snapshot coverage differs from output')

        try:
            if snapshots['status'] != 'complete' or not snapshots['snapshots']:
                raise ValueError('No verified final snapshot')
            final = snapshots['snapshots'][-1]
            check_snapshot(final)
        except Exception as exc:
            # Retry transport from the CLOSED local backup, never rerun parsing.
            snapshots['transfer_errors'].append('Final snapshot retry: '+str(exc))
            final = publish_snapshot(output.parent/'checkpoints/checkpoint.sqlite',
                                     destination/'snapshots', record)
            check_snapshot(final)
            snapshots['snapshots'].append(final)
            snapshots['status'] = 'complete'
        (output/'snapshot-status.json').write_text(json.dumps(snapshots, indent=2)+'\n')
        saved['sha256']['snapshot-status.json'] = copy_verified(
            output/'snapshot-status.json', generation/'snapshot-status.json')
        saved['sha256']['verification.json'] = copy_verified(output/'verification.json', generation/'verification.json')
        # Final SQLite is independently read back to local before integrity check.
        with tempfile.TemporaryDirectory(prefix='m2-final-readback-') as tmp:
            local = Path(tmp)/'parsing.sqlite'
            shutil.copyfile(generation/'parsing.sqlite', local)
            from .snapshots import inspect_database
            if sha256(local) != saved['sha256']['parsing.sqlite']:
                raise ValueError('Final Drive SQLite readback checksum mismatch')
            info = inspect_database(local)
            if info['publications'] != verification['publications'] or info['checkpoints'] != verification['publications']:
                raise ValueError('Final Drive SQLite coverage differs')
        require_mount(record)
        receipt = {'status': 'complete', 'directory': str(generation),
                   'source_sha256': expected_source, 'sha256': saved['sha256'],
                   'verification': verification, 'final_snapshot': final,
                   'measurement_limits': snapshots['measurement_limits']}
        local = output/'handoff.json'
        local.write_text(json.dumps(receipt, indent=2)+'\n')
        copy_verified(local, destination/'handoff.json')
        return receipt
    except Exception as exc:
        error = output/'validation-error.json'
        error.write_text(json.dumps({'status': 'incomplete', 'error': str(exc),
                                     'artifacts_directory': str(generation)}, indent=2)+'\n')
        copy_verified(error, generation/'validation-error.json')
        raise
