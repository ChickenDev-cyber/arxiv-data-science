# Module 2 — Khang: LaTeX standardization

Clean prose, normalize math and return sentence leaves. The default sentence
backend is **SaT `sat-3l-sm` on ONNX CPU**, selected by the project owner.
This is a deployment choice, not a claim that SaT wins our evaluation.
See [comparison](../../../reports/module2-tokenizer-comparison.md).

## Setup

Tested with Python **3.12**. Run from the repository root:

```bash
python -m pip install -r src/requirements.txt
PYTHONPATH=src python -m arxiv_pipeline.standardization.download_models
```

The explicit download command pins model/tokenizer revisions. It stores about
422 MiB under gitignored `data/models/`. Parser calls use local files and do not
download models or silently fall back to another algorithm.
For custom locations, set both `ARXIV_SAT_MODEL` and `ARXIV_SAT_TOKENIZER` before
setup and execution. Default paths are relative to the working directory.

## API

```python
from arxiv_pipeline.standardization import (
    clean_latex, clean_text, normalize_math, tokenize_sentences, process_prose,
)

clean_latex(r"\textbf{Result}")  # 'Result'; clean_text is the same API
normalize_math(r"Use \( x \).")  # 'Use $x$.'
sentences = process_prose(r"\textbf{Our method} works. Another result follows.")
```

- `clean_latex(text: str) -> str`: remove known layout commands, unwrap text styles;
  preserve math, citations and unfamiliar commands. Caption text is cleaned recursively.
- `normalize_math(text: str) -> str`: inline math becomes `$...$`, display math
  becomes an `equation` environment; mathematical expressions remain intact.
- `tokenize_sentences(text: str, *, backend=None) -> list[str]`: default SaT;
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
PYTHONPATH=src python -m pytest -q tests/test_sat_backend.py

# Check SaT against all legacy expectations; known failures are reported honestly.
PYTHONPATH=src python tools/evaluate_tokenizer_compatibility.py sat
```

`ARXIV_SENTENCE_BACKEND` supports `sat` (default), `rule`, `punkt`, `spacy`.
Explicit `tokenize_sentences(text, backend="rule")` overrides the environment.
Punkt/spaCy need their optional requirements and model setup documented in the
experiment reports. An invalid backend raises an error.

## Known limitations

SaT scored 9/12 diagnostic cases and passed 27/40 legacy expectations in the saved
hybrid experiment. It can merge sentences after initials at sentence end, decimal
numbers and math. Masking technical spans may remove useful context; the test
sample is small and is not a held-out corpus benchmark. Sequential inference on
the sample was slow; batching has not been benchmarked.

Unknown theorem/algorithm environments, metadata, legacy font declarations and
formatting nested in commands such as resizebox are not fully interpreted.
Malformed syntax is handled conservatively. This is not a TeX compiler.
Model/corpus binaries and local experiment outputs are not committed.
