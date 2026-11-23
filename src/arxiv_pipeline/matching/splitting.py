"""Deterministic, publication-level selection and train/valid/test split."""
from collections import Counter
from pathlib import Path
import math
import random

from .contracts import load_inventory, read_rows, validate_labels, validate_manifest, write_rows


def make_pool(inventory: Path, manual_papers: Path, output_path: Path, seed: int = 42) -> Path:
    source = load_inventory(inventory)
    manual = read_rows(manual_papers, 'manual_papers')
    by_owner = {owner: set() for owner in source['owners']}
    for row in manual:
        owner, paper = row['owner'], row['arxiv_id']
        if owner not in by_owner or source['paper_owners'].get(paper) != owner or paper in by_owner[owner]:
            raise ValueError(f'Unknown/duplicate manual paper or owner: {row}')
        by_owner[owner].add(paper)
    rows = []
    for owner, info in sorted(source['owners'].items()):
        selected = sorted(by_owner[owner])
        if len(selected) < 5:
            raise ValueError(f'{owner}: requires at least 5 manual papers')
        remaining = sorted(info['paper_ids'] - by_owner[owner])
        count = max(2, math.ceil((info['N'] - len(selected)) / 10))
        if len(remaining) < count:
            raise ValueError(f'{owner}: insufficient papers for automatic quota {count}')
        random.Random(f'{seed}:{owner}:pool').shuffle(remaining)
        rows.extend({'owner':owner, 'arxiv_id':p, 'label_method':method} for method, ids in [('manual', selected), ('automatic', remaining[:count])] for p in ids)
    return write_rows(output_path, 'pool', sorted(rows, key=lambda r:r['arxiv_id']))


def make_split(pool: Path, labels: Path, output_path: Path, seed: int = 42) -> Path:
    papers = validate_manifest(read_rows(pool, 'pool'))
    matched = Counter()
    checked = validate_labels(read_rows(labels, 'labels'), papers)
    for row in checked.values():
        if row['label_status']=='matched':
            matched[row['arxiv_id']] += 1
    result = []
    for owner in sorted({p['owner'] for p in papers.values()}):
        for method in ('manual', 'automatic'):
            ids = sorted(p for p,r in papers.items() if r['owner']==owner and r['label_method']==method)
            if method=='manual' and (len(ids)<5 or any(matched[p]<20 for p in ids)):
                raise ValueError(f'{owner}: manual quota requires 5 papers, each with 20 matching labels')
            eligible = [p for p in ids if matched[p] >= 1]
            if len(eligible)<2:
                raise ValueError(f'{owner}/{method}: needs two labeled papers for valid/test')
            random.Random(f'{seed}:{owner}:{method}:split').shuffle(eligible)
            allocation = {eligible[0]:'valid', eligible[1]:'test'}
            result.extend(dict(papers[p], partition=allocation.get(p,'train')) for p in ids)
    return write_rows(output_path, 'split', sorted(result, key=lambda r:r['arxiv_id']))
