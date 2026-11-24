"""Module C: label-free cleaning and candidate-pair features.

Every function here only sees one paper's BibTeX entries and that paper's own
reference candidates. Labels never enter feature construction, so the same
code path is valid for training, validation, test and unlabeled papers.
"""
import re
import unicodedata

import numpy as np
from rapidfuzz import fuzz, process
from scipy import sparse
from scipy.stats import rankdata
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, CountVectorizer, TfidfVectorizer
from sklearn.preprocessing import normalize as l2_normalize


FEATURE_VERSION = 'features-v1'

FEATURE_GROUPS = {
    'id': ['id_exact', 'entry_has_id', 'id_conflict'],
    'title': ['jaccard', 'contain_e_in_r', 'contain_r_in_e', 'token_set_ratio', 'partial_ratio',
              'tfidf_char_cos', 'len_ratio', 'title_exact', 'entry_title_missing'],
    'author': ['first_author_match', 'author_jaccard', 'author_overlap', 'entry_authors_missing'],
    'year': ['year_delta', 'abs_year_delta_le1', 'year_missing'],
    'group': ['title_rank_in_entry', 'gap_to_best', 'z_in_entry', 'mutual_best', 'n_candidates'],
}
FEATURE_ORDER = [name for names in FEATURE_GROUPS.values() for name in names]
INDEX = {name: i for i, name in enumerate(FEATURE_ORDER)}

# Accent commands: symbol accents may touch the letter (\"o), letter accents need a brace or space (\v{c}, \c c).
_ACCENT = re.compile(r"\\(?:[`'^\"~=.]\s*\{?|[uvHckrbdt](?:\s*\{|\s+))\s*([A-Za-z])\s*\}?")
_SPECIAL_MAP = {'ss': 'ss', 'o': 'o', 'O': 'o', 'ae': 'ae', 'AE': 'ae', 'oe': 'oe', 'OE': 'oe',
                'aa': 'a', 'AA': 'a', 'l': 'l', 'L': 'l', 'i': 'i', 'j': 'j'}
_SPECIAL = re.compile(r'\\(ss|o|O|ae|AE|oe|OE|aa|AA|l|L|i|j)(?![A-Za-z])')
_GREEK = re.compile(r'\\(?:var)?(alpha|beta|gamma|delta|epsilon|zeta|eta|theta|iota|kappa|lambda|mu|nu|xi|pi|rho|sigma|tau|upsilon|phi|chi|psi|omega|Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|Upsilon|Phi|Psi|Omega)(?![A-Za-z])')
_COMMAND = re.compile(r'\\[A-Za-z]+\*?|\\.')
_NON_WORD = re.compile(r'[\W_]+')
_AND = re.compile(r'\s+and\s+', re.IGNORECASE)
_SUFFIXES = {'jr', 'sr', 'ii', 'iii', 'iv'}
_NEW_ID = re.compile(r'(?<!\d)(?<!\d\.)(\d{2}(?:0[1-9]|1[0-2])\.\d{4,5})(?:v\d+)?(?!\d)')
_OLD_ID = re.compile(r'(?<![A-Za-z-])([a-z][a-z-]*)(?:\.[A-Za-z]{2})?/(\d{7})(?:v\d+)?(?!\d)')
_YEAR = re.compile(r'\s*(\d{4})')


def strip_latex(value):
    text = _ACCENT.sub(r'\1', value)
    text = _SPECIAL.sub(lambda m: _SPECIAL_MAP[m.group(1)], text)
    text = _GREEK.sub(r' \1 ', text)
    text = _COMMAND.sub(' ', text)
    return text.replace('~', ' ').replace('$', '').replace('{', '').replace('}', '')


def normalize_text(value):
    """LaTeX-free, accent-free, casefolded words separated by single spaces."""
    if value is None:
        return ''
    text = unicodedata.normalize('NFKD', strip_latex(str(value)))
    text = ''.join(c for c in text if not unicodedata.combining(c)).casefold()
    return ' '.join(_NON_WORD.sub(' ', text).split())


def title_tokens(value):
    return [t for t in normalize_text(value).split() if t not in ENGLISH_STOP_WORDS]


def _author_names(authors):
    if authors is None:
        return []
    items = [authors] if isinstance(authors, str) else list(authors)
    names = []
    for item in items:
        if isinstance(item, dict):
            item = item.get('name')
        if item:
            names.extend(part for part in _AND.split(str(item)) if part.strip())
    return names


def author_keys(authors):
    """(surname, first initial) per author; `et al.`/`others` dropped, collaborations kept whole."""
    keys = []
    for raw in _author_names(authors):
        text = strip_latex(raw)
        whole = normalize_text(text)
        if not whole or whole in ('others', 'et al', 'al') or whole.startswith('et al '):
            continue
        if 'collaboration' in whole.split():
            keys.append((whole, ''))
            continue
        if ',' in text:
            last, first = text.split(',', 1)
        else:
            parts = text.split()
            if len(parts) > 2 and normalize_text(parts[-1]) in _SUFFIXES:
                parts = parts[:-1]
            last, first = parts[-1], ' '.join(parts[:-1])
        surname = normalize_text(last).split()
        if surname:
            keys.append((surname[-1], normalize_text(first)[:1]))
    return keys


def extract_arxiv_id(*values):
    """First arXiv identifier in fields that mention arXiv; version suffix and subject class removed."""
    for value in values:
        if not isinstance(value, str) or 'arxiv' not in value.lower():
            continue
        match = _NEW_ID.search(value)
        if match:
            return match.group(1)
        match = _OLD_ID.search(value)
        if match:
            return f'{match.group(1)}/{match.group(2)}'
    return None


def parse_year(value):
    if value is None:
        return None
    if isinstance(value, (int, np.integer)):
        year = int(value)
    else:
        match = _YEAR.match(str(value))
        year = int(match.group(1)) if match else None
    return year if year is not None and 1900 <= year <= 2100 else None


class TitleVectorizer:
    """Character 3-5 gram TF-IDF fitted on training titles; serialized as plain JSON."""

    NGRAMS = (3, 5)

    def __init__(self, terms, idf):
        if len(terms) != len(idf):
            raise ValueError('terms and idf lengths differ')
        self.terms = list(terms)
        self.idf = np.asarray(idf, dtype=np.float64)
        self._counter = None
        if self.terms:
            self._counter = CountVectorizer(analyzer='char_wb', ngram_range=self.NGRAMS, lowercase=False,
                                            vocabulary={t: i for i, t in enumerate(self.terms)}, dtype=np.float64)

    @classmethod
    def fit(cls, titles, max_features=200_000, min_df=2):
        docs = sorted({d for d in (normalize_text(t) for t in titles) if d})
        if not docs:
            return cls([], [])
        for df in (min_df, 1):
            try:
                tfidf = TfidfVectorizer(analyzer='char_wb', ngram_range=cls.NGRAMS, lowercase=False, min_df=df,
                                        max_features=max_features, sublinear_tf=True, dtype=np.float64).fit(docs)
                break
            except ValueError:
                if df == 1:
                    return cls([], [])
        order = sorted(tfidf.vocabulary_, key=tfidf.vocabulary_.get)
        return cls(order, tfidf.idf_.tolist())

    def transform(self, titles):
        docs = [normalize_text(t) for t in titles]
        if self._counter is None:
            return sparse.csr_matrix((len(docs), 0), dtype=np.float64)
        counts = self._counter.transform(docs).tocsr()
        counts.data = 1.0 + np.log(counts.data)
        return l2_normalize(counts @ sparse.diags(self.idf), norm='l2', copy=False).tocsr()

    def to_json(self):
        return {'kind': 'char_wb_tfidf', 'ngram_range': list(self.NGRAMS), 'sublinear_tf': True,
                'terms': self.terms, 'idf': self.idf.tolist()}

    @classmethod
    def from_json(cls, data):
        if data.get('kind') != 'char_wb_tfidf' or tuple(data.get('ngram_range', ())) != cls.NGRAMS:
            raise ValueError('Unsupported TitleVectorizer payload')
        return cls(data['terms'], data['idf'])


def _binary(sets_a, sets_b):
    """Binary incidence matrices over the union vocabulary of two lists of sets."""
    vocab = {}
    for group in (sets_a, sets_b):
        for items in group:
            for item in items:
                vocab.setdefault(item, len(vocab))

    def build(sets):
        rows = [i for i, items in enumerate(sets) for _ in items]
        cols = [vocab[item] for items in sets for item in items]
        return sparse.csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(len(sets), max(len(vocab), 1)))
    return build(sets_a), build(sets_b)


def _set_overlap(sets_a, sets_b):
    a, b = _binary(sets_a, sets_b)
    inter = (a @ b.T).toarray()
    size_a = np.array([len(s) for s in sets_a], dtype=np.float64)[:, None]
    size_b = np.array([len(s) for s in sets_b], dtype=np.float64)[None, :]
    return inter, size_a, size_b


def _safe_div(num, den):
    return np.divide(num, den, out=np.zeros(np.broadcast(num, den).shape), where=den > 0)


def entry_view(entry, mask_ids=False):
    title = normalize_text(entry.get('title'))
    return {
        'title': title,
        'tokens': {t for t in title.split() if t not in ENGLISH_STOP_WORDS},
        'authors': author_keys(entry.get('authors')),
        'year': parse_year(entry.get('year')),
        'id': None if mask_ids else extract_arxiv_id(entry.get('url'), entry.get('doi'), entry.get('journal'), entry.get('title')),
    }


def ref_view(ref):
    title = normalize_text(ref.get('ref_title'))
    return {
        'title': title,
        'tokens': {t for t in title.split() if t not in ENGLISH_STOP_WORDS},
        'authors': author_keys(ref.get('ref_authors')),
        'year': parse_year(ref.get('ref_submission_date')),
        'id': ref.get('ref_arxiv_id'),
    }


def build_features(paper_entries, paper_refs, vectorizer, mask_ids=False):
    """Feature matrix of shape (m*n, len(FEATURE_ORDER)), entry-major, in the given entry/ref order."""
    m, n = len(paper_entries), len(paper_refs)
    out = np.zeros((m, n, len(FEATURE_ORDER)), dtype=np.float64)
    if m == 0 or n == 0:
        return out.reshape(m * n, len(FEATURE_ORDER))
    E = [entry_view(e, mask_ids) for e in paper_entries]
    R = [ref_view(r) for r in paper_refs]

    def put(name, value):
        out[:, :, INDEX[name]] = value

    # id
    e_ids = np.array([e['id'] or '' for e in E], dtype=object)[:, None]
    r_ids = np.array([r['id'] for r in R], dtype=object)[None, :]
    has_id = (e_ids != '').astype(np.float64)
    exact = ((e_ids == r_ids) & (e_ids != '')).astype(np.float64)
    put('id_exact', exact)
    put('entry_has_id', np.broadcast_to(has_id, (m, n)))
    put('id_conflict', has_id * (1.0 - exact))

    # title
    e_titles = [e['title'] for e in E]
    r_titles = [r['title'] for r in R]
    e_missing = np.array([not t for t in e_titles], dtype=np.float64)[:, None]
    r_missing = np.array([not t for t in r_titles], dtype=np.float64)[None, :]
    present = (1.0 - e_missing) * (1.0 - r_missing)
    inter, size_e, size_r = _set_overlap([e['tokens'] for e in E], [r['tokens'] for r in R])
    put('jaccard', _safe_div(inter, size_e + size_r - inter))
    put('contain_e_in_r', _safe_div(inter, np.broadcast_to(size_e, (m, n))))
    put('contain_r_in_e', _safe_div(inter, np.broadcast_to(size_r, (m, n))))
    put('token_set_ratio', process.cdist(e_titles, r_titles, scorer=fuzz.token_set_ratio, dtype=np.float64, workers=1) / 100.0 * present)
    put('partial_ratio', process.cdist(e_titles, r_titles, scorer=fuzz.partial_ratio, dtype=np.float64, workers=1) / 100.0 * present)
    tfidf = (vectorizer.transform(e_titles) @ vectorizer.transform(r_titles).T).toarray()
    put('tfidf_char_cos', np.clip(tfidf, 0.0, 1.0))
    put('len_ratio', _safe_div(np.minimum(size_e, size_r), np.maximum(size_e, size_r)))
    put('title_exact', (np.array(e_titles, dtype=object)[:, None] == np.array(r_titles, dtype=object)[None, :]) * present)
    put('entry_title_missing', np.broadcast_to(e_missing, (m, n)))

    # author
    e_first = np.array([e['authors'][0][0] if e['authors'] else '' for e in E], dtype=object)[:, None]
    r_first = np.array([r['authors'][0][0] if r['authors'] else '' for r in R], dtype=object)[None, :]
    put('first_author_match', ((e_first == r_first) & (e_first != '')).astype(np.float64))
    inter, size_e, size_r = _set_overlap([{k[0] for k in e['authors']} for e in E], [{k[0] for k in r['authors']} for r in R])
    put('author_jaccard', _safe_div(inter, size_e + size_r - inter))
    put('author_overlap', inter)
    put('entry_authors_missing', np.broadcast_to((size_e == 0).astype(np.float64), (m, n)))

    # year
    e_year = np.array([e['year'] if e['year'] is not None else np.nan for e in E], dtype=np.float64)[:, None]
    r_year = np.array([r['year'] if r['year'] is not None else np.nan for r in R], dtype=np.float64)[None, :]
    delta = e_year - r_year
    known = np.isfinite(delta)
    put('year_delta', np.where(known, delta, 0.0))
    put('abs_year_delta_le1', (known & (np.abs(np.where(known, delta, 99.0)) <= 1)).astype(np.float64))
    put('year_missing', (~known).astype(np.float64))

    # group: relative standing of each candidate within its entry, from a label-free title similarity
    sim = np.maximum(out[:, :, INDEX['tfidf_char_cos']], out[:, :, INDEX['token_set_ratio']])
    row_best = sim.max(axis=1, keepdims=True)
    col_best = sim.max(axis=0, keepdims=True)
    put('title_rank_in_entry', rankdata(-sim, method='min', axis=1))
    put('gap_to_best', row_best - sim)
    std = sim.std(axis=1, keepdims=True)
    put('z_in_entry', _safe_div(sim - sim.mean(axis=1, keepdims=True), np.broadcast_to(std, (m, n))))
    put('mutual_best', ((sim == row_best) & (sim == col_best) & (sim > 0)).astype(np.float64))
    put('n_candidates', np.full((m, n), float(n)))
    return out.reshape(m * n, len(FEATURE_ORDER))


def masked(features):
    """Copy with every identifier signal removed, as if url/doi/ID were absent."""
    copy = np.array(features, dtype=np.float64, copy=True)
    for name in FEATURE_GROUPS['id']:
        copy[:, INDEX[name]] = 0.0
    return copy
