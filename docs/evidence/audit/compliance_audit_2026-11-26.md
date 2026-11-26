# Compliance Audit Against Course Lab Book (`intro-ds`)

- **Audit date:** 2026-11-26
- **Audited commit:** `8345a27` (`main`)
- **Course specification:** https://hcmus-cs-ta.github.io/lab-book/intro-ds/ (`statement`, `crawling`, `parsing`, `matching`)
- **Group:** ChickenDev ($k = 5$ members, $N = 25{,}000$ official papers across 5 assigned arXiv ID ranges)

---

## 1. High-Priority Submission & Rubric Gaps

| # | Area | Finding | Course Requirement | Evidence / Reference |
|---|---|---|---|---|
| 1 | **Root `README.md`** | Missing M3 Feature Engineering justifications + supporting data analysis, missing Module A/D docs, missing end-to-end M2 runner/Colab guide, broken links to gitignored `docs/superpowers/...` and `context/crawling_new.md`, and backend default contradiction (`rule` vs `spaCy`). | M2/M3 `README.md` must document setup, execution, label format, and **feature justification illustrated with data analysis**. | `README.md` (title still Milestone 1; L635–637, L831, L1087–1264); `src/arxiv_pipeline/standardization/README.md:4`; `src/arxiv_pipeline/standardization/tokenizer.py:1,9,12` |
| 2 | **Source Repo Cleanliness** | Git currently tracks non-source/data/binary files: `manual/*.parquet` (5 files), `templates/manual_labeling_template.{csv,parquet}`, mock data under `runs/`, `statistics/`, `benchmarks/`, static PNGs in `reports/figures/`, and a copy of `jupyter-book/` (36 files incl. static PNG/SVG). Also note untracked local SSH private key at `benchmarks/azure-controller/id_ed25519` (gitignored — never commit/package). | Code repository should contain only source files (`.py`, `.ipynb`, `src/requirements.txt`, `README.md`) and exclude data files and non-source materials. | `git ls-files` across `manual/`, `templates/`, `runs/`, `statistics/`, `benchmarks/`, `reports/figures/`, `jupyter-book/` |
| 3 | **`requirements.txt` Fragmentation** | `src/requirements.txt` lacks M3 packages (`scipy`, `scikit-learn`, `rapidfuzz`, `lightgbm`); `src/requirements-m3.txt` omits `pandas` (unconditionally imported by `labeling.py:19`), `pyyaml`, and `matplotlib`, and pins `pyarrow==23.0.1` vs `26.0.1` used in actual M3 run. | `src/requirements.txt` must reproduce the environment cleanly. | `src/requirements.txt`, `src/requirements-m3.txt`, `docs/evidence/m3/training_report.json` |
| 4 | **Jupyter Book (`jupyter-book/` snapshot)** | Missing `jupyter-book/data/` (blocked by `.gitignore` `data/`) causes build `FileNotFoundError` on Sections 1.3, 1.4, 1.7; Sections 1.5, 1.6, 1.8 use static PNG/SVG + synthetic 10-paper mock tables; entire Chapter 2 (M2) consists of placeholders (*"Đang chờ code Milestone 2"*). Verify status on separate Pages repo `ChickenDev-cyber/arvix-report`. | Figures and tables must be generated at compile time from real data in a separate public Jupyter Book repo covering both M1 and M2. | `jupyter-book/book/01-crawling/{05,06,08}-*.md`, `jupyter-book/book/02-parsing/*.md`, `.gitignore:8` |
| 5 | **HF Dataset Topology & Language** | `export_owner()` writes 5 per-member HF datasets (5,000 papers each) rather than a single 25,000-paper group dataset; confirm per-member vs single-group hosting with Lab Instructors. Mixed Vietnamese/English across `README.md`, `standardization/README.md`, and exported HF dataset cards — align to official program language. | Final data hosted as a public Hugging Face dataset submitted via Moodle as a group; all submissions in program's official language; single-page portrait A2 `.pptx` poster still pending. | `src/arxiv_pipeline/matching/export.py:117-154`, `docs/evidence/m3/hf_dataset_cards/` |

---

## 2. Technical Bugs, Edge Cases & Failing Tests on `main` (`8345a27`)

### 2.1 Test Suite Status (`PYTHONPATH=src:tests pytest tests/ -q`)
- **Result:** `1,198 passed`, **`20 failed`**, `12 skipped`, `775 subtests passed` (see `pytest_summary_2026-11-26.txt`).
- **Root causes of the 20 failures:**
  - `tests/test_in_run_wait.py` & `tests/test_colab_in_run_wait.py`: `PipelineRunner.__init__()` no longer accepts `sleep` kwarg.
  - `tests/runner/test_execution_controls.py`: mock callback lacks `defer_dates` kwarg.
  - `tests/test_reference_http_diagnostics.py`: `provenance` keys `http_s2_attempts` / `http_s2_429` returned `None`.
  - `tests/parsing/test_notebook_git.py`: `status == 'rejected'` instead of `'ready'`.
  - `tests/test_matching_modeling.py::ExperimentTests`: failed on system Python lacking `lightgbm` (all **114/114** M3 tests pass inside the M3 virtualenv).

### 2.2 Milestone 1 — Data Crawling
1. **`sources/api.py:93,264,276,293`:** Exhausted transient errors (HTTP 429/5xx, network timeout) raise `retryable=False`, preventing runner-level retry passes from retrying them.
2. **`references/local_batch_runner.py:75,98,127,131,150,206`:** Validates `is_valid_arxiv_id()` *before* `normalize_arxiv_id()` (dropping `arXiv:`/`vN`/old-class IDs), calls nonexistent `date_cache.put()` instead of `put_many()`, omits `referenceCount` truncation checks, and appends `not_found` rows to `accepted_results.jsonl`.
3. **`benchmark/runner.py:254-260`:** Computes per-paper elapsed time as `max(finish) - min(start)`, inflating `mean_paper_seconds` when `stage="all"` interleaves `_sources_pass()` across all papers before `_references_pass()`.
4. **`src/m1_benchmark_colab.py:85-88`:** Preflight check uses `urllib.request.Request`, which titlecases `x-api-key` → `X-Api-Key` (defeating the `HTTPSConnection` fix in `references/api.py:38-67`).
5. **Post-backfill benchmark logs:** Nov 6 Colab handoff snapshot (`docs/evidence/m1/benchmark_summary.json`) predates final reference backfill for 3 ranges and records `cpu_only_verified: false`.

### 2.3 Milestone 2 — Hierarchical Parsing & Standardization
1. **`hierarchy/leaf_parser.py:26-28` & `hierarchy/builder.py:121,224,288`:** `normalize_math` is not invoked on `figure` leaves (captions/tables), section headings, or document titles; `clean_latex` is not invoked on section heading text.
2. **`hierarchy/builder.py:47-74`:** Missing `\part`, `\subparagraph`, `\begin{description}` (unclosed `\item` captures trailing section prose), `tabularx`, `tabulary`, `sidewaysfigure`, `sidewaystable`; `block_math_env` lacks end-tag backreference; `(?<!\\)` misses `\\\[...\]` / `\\$$...$$` after TeX line breaks; `alignat` column-count `{N}` stays inside normalized `equation`.
3. **`parsing/storage.py:385-432`:** M2 Parquet export writes `elements.kind` and `bibtex.entry_type` as `pa.string()` instead of `pa.dictionary(int32, string)` (resolved downstream at final HF export by `matching/contracts.py:32-34`).
4. **Local M2 Colab benchmark logs:** End-to-end M2 Colab CPU wall-time and peak-RAM `benchmark.json` logs are stored on Google Drive rather than inside the local workspace.

### 2.4 Milestone 3 — Reference Matching Pipeline
1. **Heuristic bugs verified empirically by E4 audit (`labeling.py:58-59, 258-283, 323-436`):**
   - `extract_arxiv_id` does not require `"arxiv"` context and does not normalize legacy subject classes (`cs.CV/...` → `cs/...`), directly causing **21 of 22 missed matches** (`explicit_arxiv_id_not_in_references`) on the 25 manual papers.
   - Tier-3 fuzzy matching (`high_similarity_match`) drops tokens with `len <= 2` (confusing `$C_6$` with `$W_4$`) and caused **all 24 of 24 false positives** (only 5/29 precision on Tier-3 matches vs 100% on Tiers 1–2).
2. **`features.py:42` & `experiments.py:39-55`:** Legacy archive regex only matches lowercase prefixes (misses `HEP-TH/...`); `-title` ablation zeroes 9 raw title features *after* `group` features were already derived from title similarity.
3. **`splitting.py` / `labeling.py:723-753`:** Enforces per-owner quotas (`>= 5` manual, `>= 20` matched/paper, `>= 10%` auto) but does not assert $k = 5$ owners (`total_manual_papers` and `total_manual_matches` are unasserted dead variables).
4. **`validation.py:105,124,439,571`:** Does not validate the HF `README.md` YAML card, leaves `inventory` parameter unused, skips cross-shard duplicate PK check on `hierarchy`, and marks `hierarchy.parent_id` as optionally nullable.
5. **Manual label status hygiene (`manual/*.parquet`):** `hoang` (26), `huynh-nhi` (127), and `trong` (89) leave non-matched rows as `unreviewed` rather than `no_match` (all 25 papers still pass the `>= 20` matched floor: min 26, total 1,880 matched).

---

## 3. Verified Strengths & Compliant Areas
- **100% Zero External Knowledge:** Strictly classical CPU methods (`LogisticRegression`, `LightGBM LambdaRank`, character n-gram TF-IDF, lexical/metadata signals) — no LLMs or pretrained embeddings.
- **Exact 6-Table Schema & Validation:** All 5 owners' 6-table Hugging Face Parquet exports (25,000 papers, 466,763 references, 11,098,189 deduplicated elements, 16,185,255 hierarchy edges, 975,674 BibTeX rows, 101,441 predictions; 1,199.45 MiB / 1.171 GiB total) passed `validate_dataset` with **0 issues**.
- **Leakage-Free Held-Out Evaluation:** Selected Logistic Regression (`C=0.1`) achieved **MRR@5 = 1.0000** on both Validation ($n = 511$) and Test ($n = 528$) under both normal and 100% ID-masked regimes, and **0.999901** across all 35,373 matched queries (only 6 disagreements total, 3 of which corrected noisy automatic heuristic labels).
