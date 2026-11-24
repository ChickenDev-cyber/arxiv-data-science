"""Module C analysis experiments (spec section 8.3, E1-E6).

Run once, after the model in `model_dir` has been chosen by `modeling.train`:

    python -m arxiv_pipeline.matching.experiments --inventory inv.json --labels labels.parquet \
        --split split.parquet --model-dir run/model --output-dir run/experiments

Everything selected here is selected on train (CV) or valid; test papers are only scored.
"""
import argparse
import copy
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from rapidfuzz import fuzz

from . import modeling
from .contracts import read_rows
from .features import FEATURE_GROUPS, FEATURE_ORDER, INDEX, normalize_text
from .modeling import (BaselineModel, LogisticModel, QuerySet, RankerModel, cross_validate, fit_config, load_matched,
                       load_model, load_papers, load_split, summarize)


def _queries(qs):
    return list(range(len(qs)))


def _evaluate(qs, models):
    """E1/E2 table: {method: {mode: summary}}."""
    every = _queries(qs)
    return {name: {'normal': summarize(qs, every, qs.reciprocal_ranks(model, every)),
                   'masked': summarize(qs, every, qs.reciprocal_ranks(model, every, mask_ids=True))}
            for name, model in models.items()}


def _without(qs, group):
    clone = copy.copy(qs)
    clone.X = qs.X.copy()
    clone.X[:, [INDEX[f] for f in FEATURE_GROUPS[group]]] = 0.0
    return clone


def ablation(train_set, config, seed):
    """E3: CV MRR@5 of the chosen config with one feature group removed (train papers only)."""
    full = cross_validate(train_set, [config], seed=seed)
    if not full:
        return {}
    out = {'full': {'normal': full[0]['normal'], 'masked': full[0]['masked']}}
    for group in FEATURE_GROUPS:
        record = cross_validate(_without(train_set, group), [config], seed=seed)[0]
        out['-' + group] = {'normal': record['normal'], 'masked': record['masked']}
    return out


def heuristic_reliability(inventory, labels, split):
    """E4: Module B heuristic against manual labels on every manual paper."""
    from .labeling import match_bibtex_entry_to_candidates
    manual = sorted(p for p, r in split.items() if r['label_method'] == 'manual')
    if not manual:
        return {}
    data = load_papers(inventory, manual)
    truth = {(r['arxiv_id'], r['citation_key']): r for r in read_rows(labels, 'labels') if r['arxiv_id'] in set(manual)}
    counts, by_evidence = Counter(), defaultdict(Counter)
    for paper, (entries, refs) in data.items():
        for entry in entries:
            label = truth.get((paper, entry['citation_key']))
            if label is None or label['label_status'] not in ('matched', 'no_match'):
                continue
            status, predicted, evidence = match_bibtex_entry_to_candidates(entry, refs)
            kind = evidence.split(':')[0]
            if status == 'matched':
                outcome = ('correct' if predicted == label['groundtruth_arxiv_id'] else 'wrong_reference') if label['label_status'] == 'matched' else 'false_match'
            else:
                outcome = 'missed' if label['label_status'] == 'matched' else 'correct_no_match'
            counts[outcome] += 1
            by_evidence[kind][outcome] += 1
    predicted_matches = counts['correct'] + counts['wrong_reference'] + counts['false_match']
    manual_matches = counts['correct'] + counts['wrong_reference'] + counts['missed']
    return {'papers': len(manual), 'counts': dict(counts),
            'precision': counts['correct'] / predicted_matches if predicted_matches else None,
            'recall': counts['correct'] / manual_matches if manual_matches else None,
            'by_evidence': {k: dict(v) for k, v in sorted(by_evidence.items())}}


def error_analysis(qs, data, model, limit=200):
    """E5: categorize valid queries whose top-1 candidate is wrong."""
    entries = {(p, e['citation_key']): e for p, (rows, _) in data.items() for e in rows}
    refs = {(p, r['ref_arxiv_id']): r for p, (_, rows) in data.items() for r in rows}
    counts, examples = Counter(), []
    for q, meta in enumerate(qs.meta):
        scores = model.predict(qs.X[qs.block(q)].astype(np.float64))
        top = modeling.rank_top5(meta['refs'], scores)
        if top[0] == meta['truth']:
            continue
        entry = entries[(meta['paper'], meta['key'])]
        wrong, right = refs[(meta['paper'], top[0])], refs[(meta['paper'], meta['truth'])]
        e_title, w_title, r_title = (normalize_text(x) for x in (entry.get('title'), wrong.get('ref_title'), right.get('ref_title')))
        e_tokens, r_tokens = set(e_title.split()), set(r_title.split())
        if not e_title:
            category = 'entry_title_missing'
        elif w_title and fuzz.token_set_ratio(w_title, r_title) >= 90:
            category = 'near_duplicate_candidates'
        elif r_tokens and len(e_tokens & r_tokens) / len(r_tokens) < 0.5:
            category = 'title_abbreviated_or_different'
        elif (wrong.get('ref_authors') or [None])[0] == (right.get('ref_authors') or [None])[0]:
            category = 'same_first_author_other_paper'
        else:
            category = 'other_check_label'
        counts[category] += 1
        if len(examples) < limit:
            examples.append({'arxiv_id': meta['paper'], 'citation_key': meta['key'], 'category': category,
                             'rank_of_truth': top.index(meta['truth']) + 1 if meta['truth'] in top else None,
                             'entry_title': entry.get('title'), 'predicted': top[0], 'predicted_title': wrong.get('ref_title'),
                             'truth': meta['truth'], 'truth_title': right.get('ref_title')})
    return {'queries': len(qs), 'errors': sum(counts.values()), 'counts': dict(counts), 'examples': examples}


def feature_profile(train_set, lr, ranker):
    """E6: per-feature separation of true vs false pairs, LR coefficients, LightGBM gain."""
    from sklearn.metrics import roc_auc_score
    y = train_set.y.astype(int)
    out = {}
    gain = ranker.booster.feature_importance(importance_type='gain') if ranker else None
    for i, name in enumerate(FEATURE_ORDER):
        column = train_set.X[:, i].astype(np.float64)
        pos, neg = column[y == 1], column[y == 0]
        out[name] = {'mean_true': float(pos.mean()) if len(pos) else None, 'mean_false': float(neg.mean()) if len(neg) else None,
                     'auc': float(roc_auc_score(y, column)) if 0 < y.sum() < len(y) and column.std() > 0 else 0.5,
                     'lr_coef': float(lr.coef[i]) if lr else None, 'lgb_gain': float(gain[i]) if gain is not None else None}
    return out


def _best_of_kind(records, kind):
    chosen, _ = modeling.select_config([r for r in records if r['kind'] == kind])
    return None if chosen is None else {k: chosen[k] for k in ('kind', 'params', 'augment')}


def _plots(results, output_dir, train_set):
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        return []
    written = []
    y = train_set.y.astype(int)
    names = ['tfidf_char_cos', 'token_set_ratio', 'author_jaccard', 'year_delta', 'gap_to_best', 'id_exact']
    fig, axes = plt.subplots(2, 3, figsize=(12, 6))
    for ax, name in zip(axes.ravel(), names):
        column = train_set.X[:, INDEX[name]]
        ax.hist([column[y == 0], column[y == 1]], bins=30, density=True, label=['false pair', 'true pair'])
        ax.set_title(name)
    axes[0, 0].legend()
    fig.tight_layout()
    fig.savefig(output_dir / 'feature_distributions.png', dpi=150)
    plt.close(fig)
    written.append('feature_distributions.png')
    ablation_rows = results.get('E3_ablation') or {}
    if ablation_rows:
        key = 'manual' if ablation_rows['full']['normal']['manual']['n'] else 'all'
        labels = list(ablation_rows)
        fig, ax = plt.subplots(figsize=(8, 4))
        x = np.arange(len(labels))
        for offset, mode in ((-0.2, 'normal'), (0.2, 'masked')):
            ax.bar(x + offset, [ablation_rows[l][mode][key]['mrr@5'] for l in labels], width=0.4, label=f'{mode} IDs')
        ax.set_xticks(x, labels)
        ax.set_ylabel(f'CV MRR@5 ({key})')
        ax.legend()
        fig.tight_layout()
        fig.savefig(output_dir / 'ablation.png', dpi=150)
        plt.close(fig)
        written.append('ablation.png')
    return written


def _fmt(value):
    return '-' if value is None else f'{value:.4f}' if isinstance(value, float) else str(value)


def _markdown(results):
    lines = ['# Module C feature and model analysis', '', f"Generated {results['generated_at']} from `{results['model_dir']}`.", '']
    for title, key in (('E1/E2 Valid MRR@5', 'valid'), ('E1/E2 Test MRR@5 (run once after selection)', 'test')):
        table = results['E1_E2'].get(key) or {}
        if not table:
            continue
        lines += [f'## {title}', '', '| method | mode | all | manual | automatic |', '|---|---|---|---|---|']
        for method, modes in table.items():
            for mode, s in modes.items():
                lines.append(f"| {method} | {mode} | {_fmt(s['all']['mrr@5'])} (n={s['all']['n']}) | {_fmt(s['manual']['mrr@5'])} | {_fmt(s['automatic']['mrr@5'])} |")
        lines.append('')
    if results.get('E3_ablation'):
        lines += ['## E3 Ablation (CV on train)', '', '| variant | normal all | normal manual | masked all | masked manual |', '|---|---|---|---|---|']
        for name, r in results['E3_ablation'].items():
            lines.append(f"| {name} | {_fmt(r['normal']['all']['mrr@5'])} | {_fmt(r['normal']['manual']['mrr@5'])} | {_fmt(r['masked']['all']['mrr@5'])} | {_fmt(r['masked']['manual']['mrr@5'])} |")
        lines.append('')
    if results.get('E4_heuristic'):
        h = results['E4_heuristic']
        lines += ['## E4 Heuristic labels vs manual labels', '', f"Papers: {h['papers']}; precision {_fmt(h['precision'])}; recall {_fmt(h['recall'])}; counts {h['counts']}.", '']
    e5 = results.get('E5_errors') or {}
    if e5:
        lines += ['## E5 Valid errors', '', f"{e5['errors']} of {e5['queries']} valid queries have a wrong top-1: {e5['counts']}.", '']
    lines += ['## E6 Features', '', '| feature | group | mean true | mean false | single-feature AUC | LR coef | LightGBM gain |', '|---|---|---|---|---|---|---|']
    group_of = {f: g for g, names in FEATURE_GROUPS.items() for f in names}
    for name, r in results['E6_features'].items():
        lines.append(f"| {name} | {group_of[name]} | {_fmt(r['mean_true'])} | {_fmt(r['mean_false'])} | {_fmt(r['auc'])} | {_fmt(r['lr_coef'])} | {_fmt(r['lgb_gain'])} |")
    if results.get('figures'):
        lines += ['', *[f'![{f}]({f})' for f in results['figures']]]
    return '\n'.join(lines) + '\n'


def run_experiments(inventory, labels, split, model_dir, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    config, vectorizer, chosen_model = load_model(model_dir)
    report = json.loads((Path(model_dir) / 'training_report.json').read_text(encoding='utf-8'))
    seed = config['seed']
    rows = load_split(split)
    method_of = {p: r['label_method'] for p, r in rows.items()}
    papers = {part: sorted(p for p, r in rows.items() if r['partition'] == part) for part in ('train', 'valid', 'test')}
    sets, data = {}, {}
    for part, ids in papers.items():
        data[part] = load_papers(inventory, ids) if ids else {}
        sets[part] = QuerySet(data[part], load_matched(labels, ids), vectorizer, method_of)

    # Alternative models of each kind, refit on train with their best CV configuration.
    every = _queries(sets['train'])
    fitted = {}
    for kind in ('logistic', 'ranker'):
        best = _best_of_kind(report.get('cv', []), kind)
        if best is None:
            continue
        same = best == {k: config['model'][k] for k in ('kind', 'params', 'augment')}
        fitted[kind] = chosen_model if same else fit_config(sets['train'], every, best, seed)
    models = {'baseline': BaselineModel(), 'model': chosen_model, **fitted}

    results = {'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'model_dir': str(model_dir),
               'selected': config['model'], 'partitions': {k: len(v) for k, v in papers.items()},
               'E1_E2': {part: _evaluate(sets[part], models) for part in ('valid', 'test') if len(sets[part])}}
    chosen_config = {k: config['model'][k] for k in ('kind', 'params', 'augment')}
    results['E3_ablation'] = ablation(sets['train'], chosen_config, seed)
    results['E4_heuristic'] = heuristic_reliability(inventory, labels, rows)
    results['E5_errors'] = error_analysis(sets['valid'], data['valid'], chosen_model)
    lr = fitted.get('logistic') if isinstance(fitted.get('logistic'), LogisticModel) else None
    ranker = fitted.get('ranker') if isinstance(fitted.get('ranker'), RankerModel) else None
    results['E6_features'] = feature_profile(sets['train'], lr, ranker)
    results['figures'] = _plots(results, output_dir, sets['train'])
    (output_dir / 'experiments.json').write_text(json.dumps(results, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    (output_dir / 'feature_analysis.md').write_text(_markdown(results), encoding='utf-8')
    return output_dir


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for name in ('inventory', 'labels', 'split', 'model-dir', 'output-dir'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args(argv)
    print(run_experiments(args.inventory, args.labels, args.split, args.model_dir, args.output_dir))


if __name__ == '__main__':
    main()
