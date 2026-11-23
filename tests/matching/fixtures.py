"""Small, explicitly synthetic Parquet corpus for module-E boundary tests."""
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


COLUMNS = {
    'papers': ['arxiv_id', 'title', 'authors', 'submission_date', 'revised_dates', 'venue'],
    'references': ['arxiv_id', 'ref_arxiv_id', 'ref_title', 'ref_authors', 'ref_submission_date', 'ref_s2_id'],
    'elements': ['element_id', 'arxiv_id', 'kind', 'content'],
    'hierarchy': ['arxiv_id', 'version', 'element_id', 'parent_id'],
    'bibtex': ['arxiv_id', 'citation_key', 'entry_type', 'title', 'authors', 'year', 'doi', 'journal', 'url'],
}


def write(path, rows, schema=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)
    return path


class Corpus:
    def __init__(self, root, n=25, owners=('trong',), entries=20):
        self.root = Path(root)
        self.inventory = self.root / 'inventory.json'
        self.info = {'schema_version': 1, 'owners': {}}
        self.ids = {}
        for idx, owner in enumerate(owners):
            ids = [f'250{idx+1}.{i+1:05}' for i in range(n)]
            self.ids[owner] = ids
            rows = {name: [] for name in COLUMNS}
            for paper in ids:
                rows['papers'].append(dict(arxiv_id=paper, title='Paper', authors=['Alice'], submission_date='2025-01-01', revised_dates=[], venue=None))
                rows['elements'].append(dict(element_id=paper+'#root', arxiv_id=paper, kind='document', content='Text'))
                rows['hierarchy'].append(dict(arxiv_id=paper, version=1, element_id=paper+'#child', parent_id=paper+'#root'))
                rows['elements'].append(dict(element_id=paper+'#child', arxiv_id=paper, kind='sentence', content='Sentence'))
                for j in range(entries):
                    ref = f'2401.{j+1:05}'
                    rows['references'].append(dict(arxiv_id=paper, ref_arxiv_id=ref, ref_title=f'Reference {j}', ref_authors=['Bob'], ref_submission_date='2024-01-01', ref_s2_id=f's2-{j}'))
                    rows['bibtex'].append(dict(arxiv_id=paper, citation_key=f'key{j}', entry_type='article', title=f'Reference {j}', authors=['Bob'], year=2024, doi='', journal='', url=''))
            tables = {}
            for name, values in rows.items():
                relative = f'inputs/{owner}/{name}/part-00000.parquet'
                fields = []
                for col in COLUMNS[name]:
                    typ = pa.list_(pa.string()) if col in ('authors', 'ref_authors', 'revised_dates') else pa.int32() if col in ('version', 'year') else pa.string()
                    fields.append(pa.field(col, typ))
                write(self.root / relative, values, pa.schema(fields))
                tables[name] = [relative]
            self.info['owners'][owner] = {'N': n, 'snapshot': 'synthetic-fixture', 'tables': tables, 'row_counts': {}, 'warnings': []}
        self.save()
        self.manual = write(self.root/'manual.parquet', [{'owner': o, 'arxiv_id': p} for o, ids in self.ids.items() for p in ids[:5]])

    def save(self):
        self.inventory.write_text(json.dumps(self.info))

    def path(self, name, owner='trong'):
        return self.root / self.info['owners'][owner]['tables'][name][0]

    def rows(self, name, owner='trong'):
        return pq.read_table(self.path(name, owner)).to_pylist()

    def replace(self, name, rows, owner='trong'):
        path = self.path(name, owner)
        return write(path, rows, pq.read_schema(path))

    def labels(self, pool):
        rows = []
        for p in pq.read_table(pool).to_pylist():
            for entry in self.rows('bibtex', p['owner']):
                if entry['arxiv_id'] == p['arxiv_id']:
                    rows.append(dict(arxiv_id=p['arxiv_id'], citation_key=entry['citation_key'], groundtruth_arxiv_id=f"2401.{int(entry['citation_key'][3:])+1:05}", label_method=p['label_method'], labeler='fixture', label_status='matched', evidence='synthetic', heuristic_version='fixture' if p['label_method']=='automatic' else None))
        return write(self.root/'labels.parquet', rows)

    def scores(self, split):
        rows = []
        for paper in pq.read_table(split).to_pylist():
            for e in self.rows('bibtex', paper['owner']):
                if e['arxiv_id'] != paper['arxiv_id']:
                    continue
                for r in self.rows('references', paper['owner']):
                    if r['arxiv_id'] == paper['arxiv_id']:
                        rows.append(dict(arxiv_id=e['arxiv_id'], citation_key=e['citation_key'], ref_arxiv_id=r['ref_arxiv_id'], score=1.0))
        return write(self.root/'scores.parquet', rows)
