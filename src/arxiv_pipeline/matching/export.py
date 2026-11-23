"""Validated ranking and portable Hugging Face Parquet delivery."""
from __future__ import annotations

from contextlib import contextmanager
import math
from pathlib import Path
import sqlite3
import tempfile

from .contracts import (load_inventory, owner_rows, paper_id, read_rows,
                        iter_rows, validate_labels, validate_manifest, write_rows)


@contextmanager
def _matching_index(inventory, labels, split, directory=None):
    source = load_inventory(inventory)
    papers = validate_manifest(read_rows(split, 'split'), split=True)
    for p, row in papers.items():
        if source['paper_owners'].get(p) != row['owner']:
            raise ValueError(f'Split ownership disagrees with inventory: {p}')
    with tempfile.TemporaryDirectory(prefix='m3-index-', dir=directory) as temporary:
        con = sqlite3.connect(Path(temporary)/'index.sqlite')
        try:
            con.execute('PRAGMA foreign_keys=ON')
            con.execute('PRAGMA synchronous=OFF')
            con.executescript('''
                CREATE TABLE entries(p TEXT, k TEXT, partition TEXT, gt TEXT, labeled INTEGER DEFAULT 0, PRIMARY KEY(p,k));
                CREATE TABLE refs(p TEXT, r TEXT, PRIMARY KEY(p,r));
                CREATE TABLE scores(p TEXT,k TEXT,r TEXT,score REAL,PRIMARY KEY(p,k,r),FOREIGN KEY(p,k) REFERENCES entries(p,k),FOREIGN KEY(p,r) REFERENCES refs(p,r));
            ''')
            for owner in source['owners']:
                for row in owner_rows(source, owner, 'bibtex'):
                    p, k = row['arxiv_id'], row['citation_key']
                    if p in papers:
                        if not k:
                            raise ValueError(f'Empty citation key: {p}')
                        con.execute('INSERT INTO entries(p,k,partition) VALUES (?,?,?)', (p,k,papers[p]['partition']))
                for row in owner_rows(source, owner, 'references'):
                    p, r = row['arxiv_id'], paper_id(row['ref_arxiv_id'])
                    if p in papers:
                        con.execute('INSERT INTO refs VALUES (?,?)', (p,r))
            checked = validate_labels(read_rows(labels, 'labels'), papers)
            for (p,k), row in checked.items():
                gt = row['groundtruth_arxiv_id']
                if gt is not None and con.execute('SELECT 1 FROM refs WHERE p=? AND r=?',(p,gt)).fetchone() is None:
                    raise ValueError(f'Ground truth outside paper references: {(p,k,gt)}')
                if con.execute('UPDATE entries SET gt=?,labeled=1 WHERE p=? AND k=?',(gt,p,k)).rowcount != 1:
                    raise ValueError(f'Label for unknown entry: {(p,k)}')
            missing = con.execute('SELECT p,k FROM entries WHERE labeled=0 LIMIT 1').fetchone()
            if missing:
                raise ValueError(f'Missing label/status for entry: {missing}')
            con.commit()
            yield con, source
        except sqlite3.IntegrityError as error:
            raise ValueError(f'Duplicate or invalid matching key: {error}') from error
        finally:
            con.close()


def check_matching_inputs(inventory: Path, labels: Path, split: Path) -> None:
    """Pre-training contract check; does not train, label or compute a metric."""
    with _matching_index(inventory, labels, split):
        pass


def build_predictions(inventory: Path, labels: Path, split: Path, scores: Path, output_path: Path) -> Path:
    output_path = Path(output_path)
    if output_path.exists():
        raise FileExistsError(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with _matching_index(inventory, labels, split, output_path.parent) as (con, _):
        try:
            for row in iter_rows(scores, 'scores'):
                if not math.isfinite(row['score']):
                    raise ValueError(f'Nonfinite score for {row["arxiv_id"]}/{row["citation_key"]}')
                con.execute('INSERT INTO scores VALUES (?,?,?,?)', (row['arxiv_id'],row['citation_key'],row['ref_arxiv_id'],row['score']))
        except sqlite3.IntegrityError as error:
            raise ValueError(f'Duplicate score or unknown entry/reference: {error}') from error
        missing = con.execute('''
            SELECT e.p,e.k FROM entries e
            LEFT JOIN (SELECT p,COUNT(*) n FROM refs GROUP BY p) r ON r.p=e.p
            LEFT JOIN (SELECT p,k,COUNT(*) n FROM scores GROUP BY p,k) s ON s.p=e.p AND s.k=e.k
            WHERE COALESCE(r.n,0) != COALESCE(s.n,0) LIMIT 1
        ''').fetchone()
        if missing:
            raise ValueError(f'Incomplete candidate score matrix for entry: {missing}')
        con.execute('CREATE INDEX ranking ON scores(p,k,score DESC,r ASC)')
        def ranked():
            for p,k,partition,gt in con.execute('SELECT p,k,partition,gt FROM entries ORDER BY p,k'):
                refs = [r[0] for r in con.execute('SELECT r FROM scores WHERE p=? AND k=? ORDER BY score DESC,r ASC LIMIT 5', (p,k))]
                refs += [None] * (5-len(refs))
                yield dict(arxiv_id=p, partition=partition, citation_key=k, groundtruth_arxiv_id=gt, **{f'cand_{i+1}':r for i,r in enumerate(refs)})
        return write_rows(output_path, 'predictions', ranked())


# Export is a separate transaction from matching; each table is streamed.
from itertools import islice
import os
import shutil

from .contracts import PUBLISHED, SHARD_ROWS


def _prediction_rows(source, predictions, owner):
    seen = set()
    for row in iter_rows(predictions, 'predictions'):
        p,k = paper_id(row['arxiv_id']), row['citation_key']
        if p not in source['paper_owners'] or row['partition'] not in ('train','valid','test'):
            raise ValueError(f'Unknown prediction paper or partition: {p}')
        if (p,k) in seen:
            raise ValueError(f'Duplicate prediction entry: {(p,k)}')
        seen.add((p,k))
        if source['paper_owners'][p] == owner:
            yield row


def export_owner(inventory: Path, predictions: Path, owner: str, output_dir: Path) -> Path:
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise FileExistsError(output_dir)
    source = load_inventory(inventory)
    if owner not in source['owners']:
        raise ValueError(f'Owner absent from inventory: {owner}')
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.'+output_dir.name+'-', dir=output_dir.parent))
    try:
        for name in PUBLISHED:
            rows = iter(_prediction_rows(source, predictions, owner) if name=='predictions' else owner_rows(source,owner,name))
            part = 0
            while True:
                batch = list(islice(rows, SHARD_ROWS))
                if not batch and part:
                    break
                write_rows(stage/'data'/name/f'part-{part:05}.parquet', name, batch)
                part += 1
                if not batch:
                    break
        configs = '\n'.join(f'- config_name: {name}\n  data_files:\n  - split: train\n    path: data/{name}/*.parquet' for name in PUBLISHED)
        (stage/'README.md').write_text(
            f'---\nconfigs:\n{configs}\n---\n\n# arXiv M1–M3 dataset — {owner}\n\n'
            'Six explicit Parquet tables. M1/M2 tables cover this owner’s corpus; '
            'predictions cover only matching-pool papers. The partition column '
            'is the ML split; the Hub storage split named train is independent.\n\n'
            'Candidates come from the citing paper’s references. Unknown ground truth '
            'is null; internal labels distinguish unmatched from unreviewed entries. '
            'Only valid matched entries contribute to MRR@5.\n', encoding='utf-8')
        # Never replace a previously published nonempty dataset.
        if output_dir.exists():
            raise FileExistsError(output_dir)
        os.rename(stage, output_dir)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return output_dir
