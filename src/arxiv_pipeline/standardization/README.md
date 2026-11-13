# Module 2 — Khang: LaTeX standardization

Clean prose, normalize math and return sentence leaves. The default sentence
backend is **spaCy `senter` on CPU**, selected after the 100-ID range benchmark.
SaT, Punkt and rule-based segmentation remain available for comparison.
See [comparison](../../../reports/module2-tokenizer-comparison.md).

## Setup

Tested with Python **3.12**. Run from the repository root:

```bash
python -m pip install -r src/requirements.txt
PYTHONPATH=src python -m arxiv_pipeline.standardization.download_models
```

The explicit setup command installs the pinned official `en_core_web_sm` **3.8.0**
wheel only when that version is missing. Parser calls use the installed model,
run only `senter`, and never download models or silently fall back.
SaT is optional: install `src/requirements-sat.txt` and run the same setup command
with `--backend sat`. Its local paths can be configured with `ARXIV_SAT_MODEL`
and `ARXIV_SAT_TOKENIZER`.

## API

```python
from arxiv_pipeline.standardization import (
    clean_latex, normalize_math, tokenize_sentences, process_prose,
)

clean_latex(r"\textbf{Result}")  # 'Result'
normalize_math(r"Use \( x \).")  # 'Use $x$.'
sentences = process_prose(r"\textbf{Our method} works. Another result follows.")
```

- `clean_latex(text: str) -> str`: remove known layout commands, unwrap text styles;
  preserve math, citations and unfamiliar commands. Caption text is cleaned recursively.
- `normalize_math(text: str) -> str`: inline math becomes `$...$`, display math
  becomes an `equation` environment; mathematical expressions remain intact.
- `tokenize_sentences(text: str, *, backend=None) -> list[str]`: default spaCy `senter`;
  return slices of original text, keeping punctuation. Protect math/commands,
  known abbreviations and URLs/emails; accept only period boundaries.
- `process_prose(text, custom_tokenizer=None) -> list[str]`: cleanup → normalize
  math → tokenize. Input is prose already separated from block math/figures by
  Module 3, with citation keys rewritten by Module 4.

Module 3 calls this module through existing `leaf_parser` entry points. It removes
comment environments, opens abstract content, and consumes direct prose labels and
proof wrappers. Formula/figure nodes remain separate from prose sentences.

## Backends and tests

```bash
# Deterministic baseline regression; no NLP model needed for these paths.
ARXIV_SENTENCE_BACKEND=rule PYTHONPATH=src python -m pytest -q \
  tests/test_standardization_tokenizer.py tests/test_standardization_math.py \
  tests/test_standardization_prose.py tests/test_tokenizer_backend.py \
  tests/test_segmentation_utils.py tests/test_prose_structure_fixes.py \
  tests/test_hierarchy_item_boundary.py tests/test_leaf_parser.py \
  tests/test_hierarchy_builder.py tests/test_hierarchy_contracts.py \
  tests/test_hierarchy_corpus.py tests/test_review_fixes.py tests/parsing

# Real selected backend smoke tests, after model setup:
PYTHONPATH=src python -m pytest -q tests/test_spacy_backend.py

# Check spaCy against all legacy expectations; known failures are reported honestly.
PYTHONPATH=src python tools/evaluate_tokenizer_compatibility.py spacy
```

`ARXIV_SENTENCE_BACKEND` supports `spacy` (default), `rule`, `punkt`, `sat`.
Explicit `tokenize_sentences(text, backend="rule")` overrides the environment.
Punkt/SaT need their optional requirements and model setup documented in the
experiment reports. An invalid backend raises an error.

## Known limitations

spaCy scored 10/12 diagnostic cases and passed 35/40 legacy expectations in the
saved hybrid experiment. It can merge sentences after sentence-final initials,
U.S., or a method name ending in a period. Masking technical spans may remove
useful context. The sample is small and is not a held-out corpus benchmark.

On 93 TeX versions from the 100-ID range sample, per-block spaCy took 17.3 seconds;
batch 32 in the benchmark harness took 9.7 seconds. **Production still calls the
model per block**, so use the per-block result (about 2.1 tokenizer-hours for
40,000 similar TeX versions) when planning. This excludes the rest of the pipeline
and is an extrapolation, not a guaranteed runtime. See the
[range benchmark](../../../reports/module2-range-benchmark-2026-11-13.md).

Unknown theorem/algorithm environments, metadata, legacy font declarations and
formatting nested in commands such as resizebox are not fully interpreted.
Malformed syntax is handled conservatively. This is not a TeX compiler.
Model/corpus binaries and local experiment outputs are not committed.

`clean_latex` is defined directly in `cleaner.py` and re-exported by the package.
Both imports work; the former `clean_text` name has been removed:

```python
from arxiv_pipeline.standardization import clean_latex
# Or: from arxiv_pipeline.standardization.cleaner import clean_latex
```
