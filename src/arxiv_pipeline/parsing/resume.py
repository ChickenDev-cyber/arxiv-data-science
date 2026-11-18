"""Verified per-publication checkpoints and consistent SQLite snapshots."""
from __future__ import annotations

import hashlib
from contextlib import closing
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time

from .benchmark import source_fingerprint


def input_fingerprint(directory: Path) -> str:
    digest=hashlib.sha256()
    for path in sorted(directory.rglob('*')):
        relative=path.relative_to(directory).as_posix()
        if path.is_dir():
            digest.update(b'dir\0'+relative.encode()+b'\0')
        elif path.is_file() and (path.name=='metadata.json' or path.suffix.lower() in {'.tex','.bib','.bbl'}):
            digest.update(b'file\0'+relative.encode()+b'\0'+path.read_bytes()+b'\0')
    return digest.hexdigest()


def _output_fingerprint(connection, aid):
    digest=hashlib.sha256()
    for table in ['elements','hierarchy','bibtex','publications_meta']:
        columns=[r[1] for r in connection.execute('PRAGMA table_info('+table+')')]
        digest.update(table.encode())
        for row in connection.execute('SELECT * FROM '+table+' WHERE arxiv_id=? ORDER BY '+','.join(columns),(aid,)):
            digest.update(json.dumps(tuple(row),ensure_ascii=False).encode()+b'\n')
    return digest.hexdigest()


class ParsingCheckpoints:
    def __init__(self, db, *, enabled=False, backup_path=None, backup_every=100):
        self.db=db;self.enabled=enabled;self.backup_path=Path(backup_path).resolve() if backup_path else None
        self.backup_every=backup_every;self.completed=0;self.backup_seconds=0.0
        if backup_every < 1:raise ValueError('backup_every must be positive')
        if self.backup_path and not enabled:raise ValueError('backup requires resume checkpoints')
        if enabled and os.environ.get('ARXIV_SENTENCE_BACKEND','rule')!='rule':
            raise ValueError('Verified resume currently supports backend rule only; use a fresh run for other backends')
        if self.backup_path:
            if self.backup_path==Path(db.db_path).resolve():raise ValueError('backup must differ from primary database')
            if self.backup_path.exists():
                if not self.backup_path.is_file():
                    raise ValueError('refusing to overwrite unrelated backup destination')
                with self.backup_path.open('rb') as header:
                    if header.read(16)!=b'SQLite format 3\x00':
                        raise ValueError('refusing to overwrite unrelated backup destination')
                with closing(sqlite3.connect(self.backup_path.as_uri()+'?mode=ro',uri=True)) as existing:
                    if not existing.execute("SELECT 1 FROM sqlite_master WHERE name='parsing_checkpoints'").fetchone():
                        raise ValueError('backup destination is not a parsing checkpoint database')
        if enabled:
            self.code=source_fingerprint()
            resources=hashlib.sha256()
            for file in sorted(Path(__file__).parents[1].rglob('*.txt')):
                resources.update(file.name.encode()+file.read_bytes())
            self.config=hashlib.sha256(json.dumps({'backend':'rule','python':sys.version,
                'resources':resources.hexdigest()},sort_keys=True).encode()).hexdigest()
            self.db.conn.execute('''CREATE TABLE IF NOT EXISTS parsing_checkpoints (
                paper_key TEXT PRIMARY KEY, arxiv_id TEXT NOT NULL, input_sha TEXT NOT NULL,
                code_sha TEXT NOT NULL, config_sha TEXT NOT NULL, output_sha TEXT NOT NULL)''')
            self.db.commit()

    def __enter__(self):return self

    def reusable(self,key,input_sha):
        row=self.db.conn.execute('SELECT arxiv_id,input_sha,code_sha,config_sha,output_sha FROM parsing_checkpoints WHERE paper_key=?',(key,)).fetchone()
        return bool(row and tuple(row[1:4])==(input_sha,self.code,self.config)
                    and row[4]==_output_fingerprint(self.db.conn,row[0]))

    def mark(self,key,aid,input_sha):
        digest=_output_fingerprint(self.db.conn,aid)
        self.db.conn.execute('INSERT OR REPLACE INTO parsing_checkpoints VALUES (?,?,?,?,?,?)',
                            (key,aid,input_sha,self.code,self.config,digest))
        self.db.commit()
        self.completed+=1
        if self.backup_path and self.completed%self.backup_every==0:self.backup()

    def backup(self):
        if not self.backup_path:return
        started=time.perf_counter();self.backup_path.parent.mkdir(parents=True,exist_ok=True)
        fd,name=tempfile.mkstemp(prefix='.parsing-backup-',suffix='.sqlite',dir=self.backup_path.parent)
        os.close(fd);temp=Path(name)
        try:
            snapshot=sqlite3.connect(temp)
            try:
                self.db.conn.backup(snapshot)
                snapshot.execute('PRAGMA journal_mode=DELETE')
                if snapshot.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                    raise RuntimeError('snapshot integrity check failed')
            finally:
                snapshot.close()
            with temp.open('rb') as stream:
                digest=hashlib.file_digest(stream,'sha256').hexdigest()
            os.replace(temp,self.backup_path)
            checksum=self.backup_path.with_suffix(self.backup_path.suffix+'.sha256')
            fd,name=tempfile.mkstemp(prefix='.parsing-hash-',dir=checksum.parent)
            with os.fdopen(fd,'w') as stream:
                stream.write(digest+'\n');stream.flush();os.fsync(stream.fileno())
            os.replace(name,checksum)
        finally:
            temp.unlink(missing_ok=True)
            self.backup_seconds+=time.perf_counter()-started

    def __exit__(self,exc_type,exc,tb):
        if not self.enabled:return
        if exc_type is not None:self.db.conn.rollback()
        try:self.backup()
        except Exception:
            if exc_type is None:raise
            logging.getLogger(__name__).exception('Final checkpoint backup failed')


def restore_database(source: Path, destination: Path) -> None:
    """Restore to a new DB, checking the optional published snapshot checksum."""
    if destination.exists():raise ValueError('restore destination must be new')
    checksum=source.with_suffix(source.suffix+'.sha256')
    if checksum.exists():
        with source.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        if digest!=checksum.read_text().strip():raise ValueError('snapshot checksum mismatch')
    with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)) as snapshot:
        if snapshot.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
            raise ValueError('snapshot integrity check failed')
        with closing(sqlite3.connect(destination)) as target:
            snapshot.backup(target)
