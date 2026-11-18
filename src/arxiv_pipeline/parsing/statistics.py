"""Export requirement statistics without treating empty versions as parser errors."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import sqlite3

from .runner import discover_paper_dirs


def export_statistics(corpus: Path, database: Path, output: Path) -> dict:
    """Compare every input publication/version with the persisted parsing result.

    An execution success means a publication record was saved, not semantic
    correctness of its entire content. Empty is the parser's recorded state;
    it must not be interpreted as a confirmed PDF-only upstream submission.
    """
    output.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    records = []
    try:
        for directory in discover_paper_dirs(corpus):
            metadata = json.loads((directory/'metadata.json').read_text()) if (directory/'metadata.json').is_file() else {}
            aid = metadata.get('arxiv_id', directory.name)
            source = directory/'sources' if (directory/'sources').is_dir() else directory
            declared = sorted({int(v['version']) for v in metadata.get('versions', [])}
                              or {int(p.name[1:]) for p in source.iterdir()
                                  if p.is_dir() and p.name.startswith('v') and p.name[1:].isdigit()}
                              or {1})
            meta = con.execute('SELECT * FROM publications_meta WHERE arxiv_id=?', (aid,)).fetchone()
            empty = json.loads(meta['empty_versions']) if meta else []
            parsed = []
            for element in con.execute("SELECT element_id FROM elements WHERE arxiv_id=? AND kind='document'", (aid,)):
                suffix = element['element_id'].rsplit('#root', 1)[-1]
                if suffix.isdigit():
                    parsed.append(int(suffix))
            parsed = sorted(set(parsed))
            accounted = set(parsed)|set(empty)
            counts = {table: con.execute('SELECT COUNT(*) FROM '+table+' WHERE arxiv_id=?', (aid,)).fetchone()[0]
                      for table in ('elements', 'hierarchy', 'bibtex')}
            records.append({
                'arxiv_id': aid, 'execution_success': meta is not None,
                'declared_versions': declared, 'parsed_versions': parsed, 'empty_versions': empty,
                'unaccounted_versions': sorted(set(declared)-accounted),
                'unexpected_versions': sorted(accounted-set(declared)),
                'parsed_version_fraction': len(set(parsed)&set(declared))/len(declared),
                'version_accounted_rate': len(accounted&set(declared))/len(declared),
                'elements_before_dedup': meta['raw_element_count'] if meta else None,
                'elements_after_dedup': counts['elements'], 'hierarchy_edges': counts['hierarchy'],
                'bibtex_before_dedup': meta['raw_bibtex_count'] if meta else None,
                'bibtex_after_dedup': counts['bibtex'],
            })
    finally:
        con.close()
    total_versions = sum(len(r['declared_versions']) for r in records)
    accounted = sum(len(set(r['declared_versions'])-set(r['unaccounted_versions'])) for r in records)
    parsed_count = sum(len(set(r['parsed_versions'])&set(r['declared_versions'])) for r in records)
    summary = {
        'attempted_publications': len(records),
        'execution_success_rate': sum(r['execution_success'] for r in records)/len(records) if records else None,
        'parsing_success_definition': 'publication result saved without an exception; not a semantic accuracy score',
        'declared_versions': total_versions,
        'parsed_version_fraction': parsed_count/total_versions if total_versions else None,
        'version_accounted_rate': accounted/total_versions if total_versions else None,
        'empty_versions': sum(len(r['empty_versions']) for r in records),
        'per_publication_file': 'publications.json',
    }
    for key in ('elements_before_dedup','elements_after_dedup','bibtex_before_dedup','bibtex_after_dedup'):
        summary[key] = sum(r[key] or 0 for r in records)
    (output/'publications.json').write_text(json.dumps(records, indent=2)+'\n')
    with (output/'publications.csv').open('w', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=list(records[0]) if records else ['arxiv_id'])
        writer.writeheader()
        for record in records:
            writer.writerow({k: json.dumps(v) if isinstance(v, list) else v for k,v in record.items()})
    (output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    return summary
