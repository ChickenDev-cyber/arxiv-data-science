"""Version-one artifact contracts shared by matching modules A–E."""
from __future__ import annotations

import errno
import json
import os
import re
import tempfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

OWNERS = ('trong', 'huynh-nhi', 'man-nhi', 'khang', 'hoang')
METHODS = ('manual', 'automatic')
PARTITIONS = ('train', 'valid', 'test')
STATUSES = ('matched', 'no_match', 'ambiguous', 'unreviewed')
BATCH_SIZE = 8192
SHARD_ROWS = 250_000
S = pa.string()
D = pa.dictionary(pa.int32(), S)
L = pa.list_(S)


def fields(*items):
    return pa.schema([pa.field(name, typ, nullable) for name, typ, nullable in items])


SCHEMAS = {
    'papers': fields(('arxiv_id',S,False), ('title',S,True), ('authors',L,True), ('submission_date',S,True), ('revised_dates',L,True), ('venue',S,True)),
    'references': fields(('arxiv_id',S,False), ('ref_arxiv_id',S,False), ('ref_title',S,True), ('ref_authors',L,True), ('ref_submission_date',S,True), ('ref_s2_id',S,True)),
    'elements': fields(('element_id',S,False), ('arxiv_id',S,False), ('kind',D,True), ('content',S,True)),
    'hierarchy': fields(('arxiv_id',S,False), ('version',pa.int32(),False), ('element_id',S,False), ('parent_id',S,False)),
    'bibtex': fields(('arxiv_id',S,False), ('citation_key',S,False), ('entry_type',D,True), ('title',S,True), ('authors',L,True), ('year',pa.int32(),True), ('doi',S,True), ('journal',S,True), ('url',S,True)),
    'manual_papers': fields(('owner',S,False), ('arxiv_id',S,False)),
    'pool': fields(('owner',S,False), ('arxiv_id',S,False), ('label_method',S,False)),
    'split': fields(('owner',S,False), ('arxiv_id',S,False), ('label_method',S,False), ('partition',S,False)),
    'labels': fields(('arxiv_id',S,False), ('citation_key',S,False), ('groundtruth_arxiv_id',S,True), ('label_method',S,False), ('labeler',S,False), ('label_status',S,False), ('evidence',S,False), ('heuristic_version',S,True)),
    'scores': fields(('arxiv_id',S,False), ('citation_key',S,False), ('ref_arxiv_id',S,False), ('score',pa.float64(),False)),
    'predictions': fields(('arxiv_id',S,False), ('partition',D,False), ('citation_key',S,False), ('groundtruth_arxiv_id',S,True), *[(f'cand_{i}',S,True) for i in range(1,6)]),
}
TABLES = ('papers', 'references', 'elements', 'hierarchy', 'bibtex')
PUBLISHED = (*TABLES, 'predictions')


def paper_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:\d{4}\.\d{4,5}|[a-zA-Z][a-zA-Z0-9.-]*(?:\.[A-Z]{2})?/\d{7})', value):
        raise ValueError(f'Expected canonical unversioned arXiv ID, got {value!r}')
    return value


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=path.parent, prefix='.'+path.name+'-')
    try:
        with os.fdopen(handle, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)
    return path


def check_columns(actual, expected, path):
    if actual.names != expected.names:
        raise ValueError(f'{path}: expected columns {expected.names}, got {actual.names}')
    for field, want in zip(actual, expected):
        source = field.type.value_type if pa.types.is_dictionary(field.type) else field.type
        target = want.type.value_type if pa.types.is_dictionary(want.type) else want.type
        if source != target and not (pa.types.is_integer(source) and pa.types.is_integer(target)):
            raise ValueError(f'{path}: incompatible type for {want.name}: {field.type}, expected {want.type}')


def iter_rows(path, name):
    schema = SCHEMAS[name]
    path = Path(path)
    try:
        pf = pq.ParquetFile(path)
        check_columns(pf.schema_arrow, schema, path)
        for batch in pf.iter_batches(batch_size=BATCH_SIZE):
            table = pa.Table.from_batches([batch]).cast(schema)
            for field in schema:
                if not field.nullable and table[field.name].null_count:
                    raise ValueError(f'{path}: null in required field {field.name}')
            yield from table.to_pylist()
    except (OSError, pa.ArrowException) as error:
        raise ValueError(f'{path}: cannot read {name}: {error}') from error


def read_rows(path, name):
    return list(iter_rows(path, name))


def write_rows(path, name, rows):
    """Stream one atomic Parquet artifact; never replace an existing artifact."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix='.parquet')
    os.close(fd)
    try:
        with pq.ParquetWriter(temporary, SCHEMAS[name], compression='snappy') as writer:
            batch = []
            for row in rows:
                batch.append(row)
                if len(batch) >= BATCH_SIZE:
                    writer.write_table(pa.Table.from_pylist(batch, schema=SCHEMAS[name]))
                    batch = []
            if batch:
                writer.write_table(pa.Table.from_pylist(batch, schema=SCHEMAS[name]))
        # Prefer no-clobber hard-link publication. Drive/FUSE implementations
        # may not support links: reserve the destination exclusively, then
        # replace that reservation with the complete file.
        try:
            os.link(temporary, path)
        except OSError as error:
            if error.errno not in (errno.EOPNOTSUPP, errno.ENOSYS, errno.EXDEV, errno.EPERM):
                raise
            reservation = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
            os.close(reservation)
            try:
                os.replace(temporary, path)
            except BaseException:
                path.unlink(missing_ok=True)
                raise
    finally:
        Path(temporary).unlink(missing_ok=True)
    return path


def load_inventory(path):
    path = Path(path).resolve()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise ValueError(f'Cannot read inventory {path}: {error}') from error
    owners = data.get('owners')
    if data.get('schema_version') != 1 or not isinstance(owners, dict) or not owners:
        raise ValueError('inventory requires schema_version=1 and nonempty owners')
    resolved, paper_owners = {}, {}
    for owner, info in sorted(owners.items()):
        if owner not in OWNERS or not isinstance(info, dict):
            raise ValueError(f'Invalid inventory owner {owner!r}')
        if type(info.get('N')) is not int or info['N'] < 1 or not isinstance(info.get('snapshot'), str) or not info['snapshot']:
            raise ValueError(f'{owner}: positive integer N and snapshot are required')
        files = {}
        for table in TABLES:
            values = info.get('tables', {}).get(table)
            if not isinstance(values, list) or not values or any(not isinstance(v,str) for v in values):
                raise ValueError(f'{owner}: missing paths for {table}')
            paths = [(path.parent / v).resolve() for v in values]
            if len(set(paths)) != len(paths):
                raise ValueError(f'{owner}: duplicate shard for {table}')
            for source in paths:
                try:
                    check_columns(pq.read_schema(source), SCHEMAS[table], source)
                except (OSError, pa.ArrowException) as error:
                    raise ValueError(f'{owner}/{table}: missing or unreadable {source}') from error
            files[table] = paths
        ids = set()
        for source in files['papers']:
            for row in iter_rows(source, 'papers'):
                identifier = paper_id(row['arxiv_id'])
                if identifier in ids or identifier in paper_owners:
                    raise ValueError(f'Duplicate or conflicting paper ownership: {identifier}')
                ids.add(identifier)
        if len(ids) != info['N']:
            raise ValueError(f'{owner}: N={info["N"]} disagrees with {len(ids)} papers')
        paper_owners.update({identifier: owner for identifier in ids})
        resolved[owner] = dict(info, tables=files, paper_ids=ids)
    inventory = {'path': path, 'owners': resolved, 'paper_owners': paper_owners}
    for owner, info in resolved.items():
        counts = info.get('row_counts', {})
        if not isinstance(counts, dict) or set(counts) - set(TABLES):
            raise ValueError(f'{owner}: invalid row_counts table names')
        for table, expected in counts.items():
            if type(expected) is not int or expected < 0:
                raise ValueError(f'{owner}: row_counts {table} must be a nonnegative integer')
            actual = _owner_row_count(inventory, owner, table)
            if actual != expected:
                raise ValueError(f'{owner}: row_counts {table}: expected {expected}, actual {actual}')
    return inventory


def _owner_row_count(inventory, owner, table):
    # Read only the citing-ID column, avoiding large element text payloads.
    count = 0
    for source in inventory['owners'][owner]['tables'][table]:
        try:
            for batch in pq.ParquetFile(source).iter_batches(batch_size=BATCH_SIZE, columns=['arxiv_id']):
                for value in batch.column(0).to_pylist():
                    identifier = paper_id(value)
                    actual_owner = inventory['paper_owners'].get(identifier)
                    if actual_owner is None:
                        raise ValueError(f'{source}: unknown paper {identifier} in row_counts {table}')
                    count += actual_owner == owner
        except (OSError, pa.ArrowException) as error:
            raise ValueError(f'{source}: cannot verify row_counts {table}: {error}') from error
    return count


def owner_rows(inventory, owner, table):
    for source in inventory['owners'][owner]['tables'][table]:
        for row in iter_rows(source, table):
            identifier = paper_id(row['arxiv_id'])
            if identifier not in inventory['paper_owners']:
                raise ValueError(f'{source}: {table} contains unknown paper {identifier}')
            if inventory['paper_owners'][identifier] == owner:
                yield row


def validate_manifest(rows, split=False):
    seen = {}
    for row in rows:
        p = paper_id(row['arxiv_id'])
        if p in seen or row['owner'] not in OWNERS or row['label_method'] not in METHODS:
            raise ValueError(f'Invalid/duplicate manifest paper: {p}')
        if split and row['partition'] not in PARTITIONS:
            raise ValueError(f'Invalid partition for {p}')
        seen[p] = row
    if not seen:
        raise ValueError('Manifest must not be empty')
    return seen


def validate_labels(rows, papers):
    seen = {}
    for row in rows:
        key = (paper_id(row['arxiv_id']), row['citation_key'])
        if not row['citation_key'] or key in seen or key[0] not in papers:
            raise ValueError(f'Duplicate or unknown label: {key}')
        if row['label_method'] != papers[key[0]]['label_method'] or row['label_status'] not in STATUSES:
            raise ValueError(f'Invalid label method/status: {key}')
        gt = row['groundtruth_arxiv_id']
        if (row['label_status'] == 'matched') != (gt is not None):
            raise ValueError(f'Ground truth/status disagreement: {key}')
        if gt is not None:
            paper_id(gt)
        if row['label_method']=='automatic' and row['label_status']=='unreviewed':
            raise ValueError(f'Automatic label was not processed: {key}')
        seen[key] = row
    return seen
