"""Module C: baseline, Logistic Regression and LightGBM ranker for reference matching.

Contract used by `pipeline.run_pipeline` in full mode:

* `train(inventory, labels, split, model_dir) -> Path` fits on `partition == 'train'` only,
  selects the configuration with grouped CV on train papers, confirms on valid, never reads test.
* `score(inventory, split, model_dir, output_path, method) -> Path` never reads labels and
  writes one finite score per (entry, candidate) pair of every split paper.
"""
import hashlib
import json
import os
import platform
import resource
import shutil
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from rapidfuzz import fuzz, process

from .contracts import load_inventory, owner_rows, read_rows, write_rows
from .features import (FEATURE_GROUPS, FEATURE_ORDER, FEATURE_VERSION, INDEX, TitleVectorizer, build_features,
                       masked, normalize_text)


SEED = 42
METHODS = ('baseline', 'model')
NUM_THREADS = 2
CV_FOLDS = 5
AUGMENT_RATE = 0.5
LR_GRID = ({'C': 0.1}, {'C': 1.0}, {'C': 10.0})
RANKER_GRID = (
    {'num_leaves': 15, 'learning_rate': 0.05, 'n_estimators': 300, 'min_data_in_leaf': 20},
    {'num_leaves': 31, 'learning_rate': 0.05, 'n_estimators': 500, 'min_data_in_leaf': 50},
)
BASELINE = {'kind': 'token_set_ratio', 'field': 'title', 'normalization': 'features.normalize_text',
            'scale': 0.01, 'missing_title_score': 0.0}


# ----------------------------------------------------------------------------- ranking

def rank_top5(ref_ids, scores):
    """Module E ranking rule: score descending, then ref_arxiv_id ascending."""
    order = sorted(range(len(ref_ids)), key=lambda j: (-float(scores[j]), ref_ids[j]))
    return [ref_ids[j] for j in order[:5]]


def reciprocal_rank(ref_ids, scores, truth):
    top = rank_top5(ref_ids, scores)
    return 1.0 / (top.index(truth) + 1) if truth in top else 0.0


def mrr_at_5(queries):
    """Mean reciprocal rank over (ref_ids, scores, truth) triples; None when there is no query."""
    values = [reciprocal_rank(*query) for query in queries]
    return float(np.mean(values)) if values else None


# ----------------------------------------------------------------------------- inputs

def load_split(split):
    return {row['arxiv_id']: row for row in read_rows(split, 'split')}


def load_papers(inventory, papers):
    """{paper: (entries sorted by citation_key, refs sorted by ref_arxiv_id)} for the requested papers only."""
    inv = load_inventory(inventory)
    wanted = set(papers)
    unknown = wanted - inv['paper_owners'].keys()
    if unknown:
        raise ValueError(f'Split papers missing from inventory: {sorted(unknown)[:5]}')
    entries, refs = defaultdict(list), defaultdict(list)
    for owner in sorted({inv['paper_owners'][p] for p in wanted}):
        for row in owner_rows(inv, owner, 'bibtex'):
            if row['arxiv_id'] in wanted:
                entries[row['arxiv_id']].append(row)
        for row in owner_rows(inv, owner, 'references'):
            if row['arxiv_id'] in wanted:
                refs[row['arxiv_id']].append(row)
    return {p: (sorted(entries[p], key=lambda r: r['citation_key']), sorted(refs[p], key=lambda r: r['ref_arxiv_id']))
            for p in sorted(wanted)}


def load_matched(labels, papers):
    """{(paper, citation_key): groundtruth} for matched labels of the given papers."""
    papers = set(papers)
    return {(row['arxiv_id'], row['citation_key']): row['groundtruth_arxiv_id']
            for row in read_rows(labels, 'labels')
            if row['arxiv_id'] in papers and row['label_status'] == 'matched' and row['groundtruth_arxiv_id']}


# ----------------------------------------------------------------------------- query sets

class QuerySet:
    """One query per matched entry: all candidates of its paper, exactly one relevant."""

    def __init__(self, data, matched, vectorizer, method_of):
        blocks, labels, self.meta, self.skipped = [], [], [], 0
        for paper in sorted(data):
            entries, refs = data[paper]
            picked = [(i, e) for i, e in enumerate(entries) if (paper, e['citation_key']) in matched]
            if not picked or not refs:
                self.skipped += len(picked)
                continue
            ref_ids = [r['ref_arxiv_id'] for r in refs]
            position = {r: j for j, r in enumerate(ref_ids)}
            X = build_features(entries, refs, vectorizer).reshape(len(entries), len(refs), len(FEATURE_ORDER))
            for i, entry in picked:
                truth = matched[(paper, entry['citation_key'])]
                if truth not in position:
                    self.skipped += 1
                    continue
                y = np.zeros(len(refs), dtype=np.float32)
                y[position[truth]] = 1.0
                blocks.append(X[i].astype(np.float32))
                labels.append(y)
                self.meta.append({'paper': paper, 'key': entry['citation_key'], 'method': method_of[paper],
                                  'truth': truth, 'refs': ref_ids, 'has_id': bool(X[i, 0, INDEX['entry_has_id']])})
        width = len(FEATURE_ORDER)
        self.X = np.vstack(blocks) if blocks else np.zeros((0, width), dtype=np.float32)
        self.y = np.concatenate(labels) if labels else np.zeros(0, dtype=np.float32)
        sizes = np.array([len(m['refs']) for m in self.meta], dtype=np.int64)
        self.offsets = np.concatenate([[0], np.cumsum(sizes)])

    def __len__(self):
        return len(self.meta)

    def block(self, q):
        return slice(self.offsets[q], self.offsets[q + 1])

    def assemble(self, queries, augment, seed=SEED):
        """Training matrix, labels and group sizes; augmentation appends ID-masked copies of hashed queries."""
        parts, ys, groups = [], [], []
        for q in queries:
            parts.append(self.X[self.block(q)])
            ys.append(self.y[self.block(q)])
            groups.append(len(self.meta[q]['refs']))
        if augment:
            for q in queries:
                meta = self.meta[q]
                if meta['has_id'] and augment_pick(meta['paper'], meta['key'], seed):
                    parts.append(masked(self.X[self.block(q)]).astype(np.float32))
                    ys.append(self.y[self.block(q)])
                    groups.append(len(meta['refs']))
        return np.vstack(parts), np.concatenate(ys), np.array(groups, dtype=np.int64)

    def reciprocal_ranks(self, model, queries, mask_ids=False):
        if not queries:
            return []
        rows = np.concatenate([np.arange(self.offsets[q], self.offsets[q + 1]) for q in queries])
        X = self.X[rows].astype(np.float64)
        scores = model.predict(masked(X) if mask_ids else X)
        out, start = [], 0
        for q in queries:
            n = len(self.meta[q]['refs'])
            out.append(reciprocal_rank(self.meta[q]['refs'], scores[start:start + n], self.meta[q]['truth']))
            start += n
        return out


def augment_pick(paper, key, seed=SEED):
    digest = hashlib.sha256(f'{paper}\x1f{key}\x1f{seed}'.encode()).digest()
    return int.from_bytes(digest[:8], 'big') / 2 ** 64 < AUGMENT_RATE


def summarize(queryset, queries, ranks):
    """MRR@5 overall and per label method."""
    by = defaultdict(list)
    for q, rr in zip(queries, ranks):
        by['all'].append(rr)
        by[queryset.meta[q]['method']].append(rr)
    return {k: {'mrr@5': float(np.mean(by[k])) if by[k] else None, 'n': len(by[k])} for k in ('all', 'manual', 'automatic')}


# ----------------------------------------------------------------------------- models

class BaselineModel:
    kind = 'baseline'

    def predict(self, X):
        return np.asarray(X, dtype=np.float64)[:, INDEX['token_set_ratio']]


def baseline_scores(entries, refs):
    """token_set_ratio of normalized titles / 100, 0 when either title is missing."""
    e = [normalize_text(x.get('title')) for x in entries]
    r = [normalize_text(x.get('ref_title')) for x in refs]
    if not e or not r:
        return np.zeros((len(e), len(r)))
    present = np.array([bool(t) for t in e], dtype=np.float64)[:, None] * np.array([bool(t) for t in r], dtype=np.float64)[None, :]
    return process.cdist(e, r, scorer=fuzz.token_set_ratio, dtype=np.float64, workers=1) * BASELINE['scale'] * present


class LogisticModel:
    kind = 'logistic'

    def __init__(self, coef, intercept, mean, scale, params):
        self.coef = np.asarray(coef, dtype=np.float64)
        self.intercept = float(intercept)
        self.mean = np.asarray(mean, dtype=np.float64)
        self.scale = np.asarray(scale, dtype=np.float64)
        self.params = dict(params)

    @classmethod
    def fit(cls, X, y, groups, params, seed=SEED):
        from sklearn.linear_model import LogisticRegression
        X = np.asarray(X, dtype=np.float64)
        mean = X.mean(axis=0)
        scale = X.std(axis=0)
        scale[scale == 0] = 1.0
        clf = LogisticRegression(C=params['C'], class_weight='balanced', max_iter=2000, random_state=seed)
        clf.fit((X - mean) / scale, y)
        return cls(clf.coef_[0], clf.intercept_[0], mean, scale, params)

    def predict(self, X):
        return ((np.asarray(X, dtype=np.float64) - self.mean) / self.scale) @ self.coef + self.intercept

    def to_json(self):
        return {'kind': self.kind, 'feature_order': FEATURE_ORDER, 'params': self.params, 'coef': self.coef.tolist(),
                'intercept': self.intercept, 'mean': self.mean.tolist(), 'scale': self.scale.tolist()}

    @classmethod
    def from_json(cls, data):
        if data.get('feature_order') != FEATURE_ORDER:
            raise ValueError('logistic.json feature order differs from FEATURE_ORDER')
        return cls(data['coef'], data['intercept'], data['mean'], data['scale'], data['params'])


class RankerModel:
    kind = 'ranker'

    def __init__(self, booster, params):
        self.booster = booster
        self.params = dict(params)

    @staticmethod
    def lgb_params(params, seed=SEED):
        return {'objective': 'lambdarank', 'metric': 'ndcg', 'eval_at': [5], 'learning_rate': params['learning_rate'],
                'num_leaves': params['num_leaves'], 'min_data_in_leaf': params['min_data_in_leaf'],
                'seed': seed, 'deterministic': True, 'force_row_wise': True, 'num_threads': NUM_THREADS,
                'feature_pre_filter': False, 'verbose': -1}

    @classmethod
    def fit(cls, X, y, groups, params, seed=SEED):
        import lightgbm as lgb
        data = lgb.Dataset(np.asarray(X, dtype=np.float64), label=y, group=groups, feature_name=FEATURE_ORDER,
                           params={'verbose': -1, 'feature_pre_filter': False})
        booster = lgb.train(cls.lgb_params(params, seed), data, num_boost_round=params['n_estimators'])
        return cls(booster, params)

    def predict(self, X):
        return np.asarray(self.booster.predict(np.asarray(X, dtype=np.float64), num_threads=NUM_THREADS), dtype=np.float64)

    def to_string(self):
        return self.booster.model_to_string()

    @classmethod
    def from_string(cls, text, params):
        import lightgbm as lgb
        booster = lgb.Booster(model_str=text)
        if booster.feature_name() != FEATURE_ORDER:
            raise ValueError('ranker.txt feature order differs from FEATURE_ORDER')
        return cls(booster, params)


MODEL_CLASSES = {'logistic': LogisticModel, 'ranker': RankerModel}


def candidate_configs():
    configs = [('logistic', p) for p in LR_GRID]
    try:
        import lightgbm  # noqa: F401
        configs += [('ranker', p) for p in RANKER_GRID]
    except ImportError:
        pass
    return [{'kind': kind, 'params': dict(params), 'augment': augment}
            for augment in (False, True) for kind, params in configs]


def fit_config(queryset, queries, config, seed=SEED):
    X, y, groups = queryset.assemble(queries, config['augment'], seed)
    return MODEL_CLASSES[config['kind']].fit(X, y, groups, config['params'], seed)


# ----------------------------------------------------------------------------- selection

def cross_validate(queryset, configs, folds=None, seed=SEED):
    """Grouped CV by paper; returns one record per config with MRR@5 in normal and ID-masked modes."""
    from sklearn.model_selection import GroupKFold
    papers = np.array([m['paper'] for m in queryset.meta])
    n_splits = min(folds or CV_FOLDS, len(set(papers)))
    if n_splits < 2:
        return []
    splits = list(GroupKFold(n_splits=n_splits).split(np.zeros(len(papers)), groups=papers))
    records = []
    for order, config in enumerate(configs):
        normal, hidden, held = [], [], []
        for train_idx, test_idx in splits:
            model = fit_config(queryset, train_idx.tolist(), config, seed)
            held.extend(test_idx.tolist())
            normal.extend(queryset.reciprocal_ranks(model, test_idx.tolist()))
            hidden.extend(queryset.reciprocal_ranks(model, test_idx.tolist(), mask_ids=True))
        records.append(dict(config, order=order, folds=n_splits,
                            normal=summarize(queryset, held, normal), masked=summarize(queryset, held, hidden)))
    return records


def select_config(records):
    """Highest CV MRR@5 on manual entries (all entries if none), then masked MRR@5, then simpler config."""
    if not records:
        return None, None
    key = 'manual' if records[0]['normal']['manual']['n'] else 'all'

    def rank(record):
        return (round(record['normal'][key]['mrr@5'], 9), round(record['masked'][key]['mrr@5'], 9), -record['order'])
    return max(records, key=rank), f'cv_mrr@5_{key}'


# ----------------------------------------------------------------------------- persistence

def _dump(path, value, compact=False):
    text = json.dumps(value, sort_keys=True, separators=(',', ':')) if compact else json.dumps(value, sort_keys=True, indent=2)
    Path(path).write_text(text + '\n', encoding='utf-8')


def _versions():
    import pyarrow
    import rapidfuzz
    import sklearn
    out = {'python': platform.python_version(), 'numpy': np.__version__, 'scikit-learn': sklearn.__version__,
           'rapidfuzz': rapidfuzz.__version__, 'pyarrow': pyarrow.__version__}
    try:
        import lightgbm
        out['lightgbm'] = lightgbm.__version__
    except ImportError:
        out['lightgbm'] = None
    return out


def load_model(model_dir):
    """(config, vectorizer, model) after checking the feature contract."""
    model_dir = Path(model_dir)
    config = json.loads((model_dir / 'config.json').read_text(encoding='utf-8'))
    if config.get('feature_version') != FEATURE_VERSION or config.get('feature_order') != FEATURE_ORDER:
        raise ValueError(f'{model_dir}: feature_version {config.get("feature_version")!r} does not match {FEATURE_VERSION!r}')
    vectorizer = TitleVectorizer.from_json(json.loads((model_dir / 'tfidf.json').read_text(encoding='utf-8')))
    chosen = config['model']
    payload = (model_dir / chosen['file']).read_text(encoding='utf-8')
    if chosen['kind'] == 'logistic':
        model = LogisticModel.from_json(json.loads(payload))
    else:
        model = RankerModel.from_string(payload, chosen['params'])
    return config, vectorizer, model


def _peak_rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


# ----------------------------------------------------------------------------- contract

def train(inventory, labels, split, model_dir, seed=SEED):
    model_dir = Path(model_dir)
    if model_dir.exists():
        raise FileExistsError(model_dir)
    timings, started = {}, time.perf_counter()

    def tick(name, since):
        timings[name] = round(time.perf_counter() - since, 3)
        return time.perf_counter()

    t = time.perf_counter()
    rows = load_split(split)
    train_papers = sorted(p for p, r in rows.items() if r['partition'] == 'train')
    valid_papers = sorted(p for p, r in rows.items() if r['partition'] == 'valid')
    if not train_papers:
        raise ValueError('split has no train papers')
    method_of = {p: rows[p]['label_method'] for p in train_papers + valid_papers}
    train_data = load_papers(inventory, train_papers)
    matched = load_matched(labels, train_papers)
    t = tick('load_train', t)

    titles = [e.get('title') for entries, _ in train_data.values() for e in entries]
    titles += [r.get('ref_title') for _, refs in train_data.values() for r in refs]
    vectorizer = TitleVectorizer.fit(titles)
    t = tick('fit_vectorizer', t)

    train_set = QuerySet(train_data, matched, vectorizer, method_of)
    if not len(train_set):
        raise ValueError('no matched train entries with a candidate ground truth')
    t = tick('train_features', t)

    records = cross_validate(train_set, candidate_configs(), seed=seed)
    chosen, metric = select_config(records)
    if chosen is None:
        chosen, metric = {'kind': 'logistic', 'params': {'C': 1.0}, 'augment': False}, 'default_no_cv'
    t = tick('cross_validation', t)

    model = fit_config(train_set, list(range(len(train_set))), chosen, seed)
    t = tick('final_fit', t)

    valid_data = load_papers(inventory, valid_papers) if valid_papers else {}
    valid_set = QuerySet(valid_data, load_matched(labels, valid_papers), vectorizer, method_of)
    every = list(range(len(valid_set)))
    validation = {
        'model': {'normal': summarize(valid_set, every, valid_set.reciprocal_ranks(model, every)),
                  'masked': summarize(valid_set, every, valid_set.reciprocal_ranks(model, every, mask_ids=True))},
        'baseline': {'normal': summarize(valid_set, every, valid_set.reciprocal_ranks(BaselineModel(), every))},
    }
    t = tick('validation', t)

    model_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=model_dir.parent, prefix='.model-'))
    try:
        file = 'logistic.json' if chosen['kind'] == 'logistic' else 'ranker.txt'
        selected = {'kind': chosen['kind'], 'params': chosen['params'], 'augment': chosen['augment'], 'file': file}
        _dump(staging / 'config.json', {'feature_version': FEATURE_VERSION, 'feature_order': FEATURE_ORDER,
                                        'feature_groups': FEATURE_GROUPS, 'seed': seed, 'model': selected,
                                        'selection_metric': metric})
        _dump(staging / 'tfidf.json', vectorizer.to_json(), compact=True)
        if chosen['kind'] == 'logistic':
            _dump(staging / file, model.to_json())
        else:
            (staging / file).write_text(model.to_string(), encoding='utf-8')
        _dump(staging / 'baseline.json', BASELINE)
        tick('save', t)
        timings['total'] = round(time.perf_counter() - started, 3)
        _dump(staging / 'training_report.json', {
            'selected_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'selected': selected, 'selection_metric': metric, 'cv': records,
            'counts': {'train_papers': len(train_papers), 'valid_papers': len(valid_papers),
                       'train_queries': len(train_set), 'train_pairs': int(train_set.X.shape[0]),
                       'train_queries_by_method': {k: sum(m['method'] == k for m in train_set.meta) for k in ('manual', 'automatic')},
                       'train_skipped_labels': train_set.skipped, 'valid_queries': len(valid_set),
                       'tfidf_terms': len(vectorizer.terms)},
            'valid': validation, 'timings_seconds': timings, 'peak_rss_mb': round(_peak_rss_mb(), 1),
            'threads': NUM_THREADS, 'versions': _versions(),
        })
        if model_dir.exists():
            raise FileExistsError(model_dir)
        os.rename(staging, model_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return model_dir


def score(inventory, split, model_dir, output_path, method):
    if method not in METHODS:
        raise ValueError(f'method must be one of {METHODS}, got {method!r}')
    output_path = Path(output_path)
    if output_path.exists():
        raise FileExistsError(output_path)
    vectorizer = model = None
    if method == 'model':
        _, vectorizer, model = load_model(model_dir)
    data = load_papers(inventory, load_split(split))

    def rows():
        for paper, (entries, refs) in data.items():
            if not entries or not refs:
                continue
            if method == 'baseline':
                values = baseline_scores(entries, refs).ravel()
            else:
                values = model.predict(build_features(entries, refs, vectorizer))
            if values.shape != (len(entries) * len(refs),) or not np.all(np.isfinite(values)):
                raise ValueError(f'{paper}: non-finite or incomplete scores')
            for i, entry in enumerate(entries):
                for j, ref in enumerate(refs):
                    yield {'arxiv_id': paper, 'citation_key': entry['citation_key'],
                           'ref_arxiv_id': ref['ref_arxiv_id'], 'score': float(values[i * len(refs) + j])}
    return write_rows(output_path, 'scores', rows())
