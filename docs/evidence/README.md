# Captured Technical Evidence (Milestones 1–3 & Compliance Audit)

This directory archives lightweight, auditable empirical evidence backing the technical report in [`../report/main.pdf`](../report/main.pdf) (branch `feat/report`, audited against `main` commit `8345a27` on `2026-11-26`). Raw corpora, Parquet shards, model weights, and credentials are intentionally excluded.

## Directory Layout

- **`m1/`**: Final 25,000-paper Milestone 1 corpus summary (`m1_final_25k_summary.json`) plus the Nov 6, 2026 Google Colab CPU handoff measurements across 167 invocations (`benchmark_summary.json`, `owner_metrics.csv`, `invocations.csv`).
- **`m2/`**: Final 25,000-paper Milestone 2 parsing, cross-version deduplication, and BibTeX summary (`m2_final_25k_summary.json`), the 100-ID CPU tokenizer range benchmark (`range_benchmark_2026-11-13/`), and the SaT batching pilot (`sat_pilot_2026-11-13/`).
- **`m3/`**: Official 5-owner Milestone 3 full-run outputs (`run_config.json`, `label_report.json`, `training_report.json`, `experiments.json`, `feature_analysis.md`, `metrics_{model,baseline}.json`, `validation_{model,baseline}.json`, plots `ablation.png` and `feature_distributions.png`), all five owners' 6-table Hugging Face validation reports (`dataset_validation/`), exported YAML dataset cards (`hf_dataset_cards/`), programmatic manual-label audit (`manual_labels_audit.json`), and forensic root-cause breakdown of all 6 model disagreements out of 35,373 queries (`forensic_errors_6_cases.json`).
- **`audit/`**: Full compliance audit against `https://hcmus-cs-ta.github.io/lab-book/intro-ds/` (`compliance_audit_2026-11-26.md`) and test suite snapshot (`pytest_summary_2026-11-26.txt`).

## File Manifest & SHA-256 Checksums

| File | Size (bytes) | SHA-256 (prefix 16) |
|---|---:|---|
| `audit/compliance_audit_2026-11-26.md` | 9,260 | `3117d5c8d89136f9` |
| `audit/pytest_summary_2026-11-26.txt` | 2,913 | `65463be1e0ead9ee` |
| `m1/benchmark_summary.json` | 12,131 | `dadf5b107fdf60d7` |
| `m1/invocations.csv` | 50,157 | `b4f283a4ee277a5a` |
| `m1/m1_final_25k_summary.json` | 2,811 | `21e7a899406057ef` |
| `m1/owner_metrics.csv` | 1,229 | `4dbd45937d13185f` |
| `m2/m2_final_25k_summary.json` | 5,315 | `b267064fec796d19` |
| `m2/range_benchmark_2026-11-13/downloads-first-pass.json` | 19,879 | `b23bf82799bb793b` |
| `m2/range_benchmark_2026-11-13/downloads.json` | 19,982 | `9782a5f63488bd19` |
| `m2/range_benchmark_2026-11-13/environment.json` | 334 | `2b5acd4e713171cc` |
| `m2/range_benchmark_2026-11-13/metadata.json` | 868 | `549b91846050ed2d` |
| `m2/range_benchmark_2026-11-13/model-checksums.json` | 674 | `6f1271749e890cb0` |
| `m2/range_benchmark_2026-11-13/preparation.json` | 33,567 | `8d0552004fb7b8b2` |
| `m2/range_benchmark_2026-11-13/spacy-b1-ordered-n93.json` | 2,211 | `941aa47f0aa165de` |
| `m2/range_benchmark_2026-11-13/spacy-b32-ordered-n93.json` | 2,205 | `fcb8426cf0b015c5` |
| `m2/range_benchmark_2026-11-13/summary.json` | 40,866 | `146127874dc56666` |
| `m2/sat_pilot_2026-11-13/sat-b1-bucket-n3-ib32.json` | 577 | `ee11c3465a2a8c82` |
| `m2/sat_pilot_2026-11-13/sat-b1-bucket-n3.json` | 554 | `09fccbeaf64aacbd` |
| `m2/sat_pilot_2026-11-13/sat-b32-bucket-n3.json` | 553 | `3fdab1957d14ea3a` |
| `m2/sat_pilot_2026-11-13/sat-b8-bucket-n3.json` | 551 | `a8f8850718ac0428` |
| `m3/ablation.png` | 28,011 | `e900ee6628a12671` |
| `m3/dataset_validation/hoang.json` | 1,717 | `fbbcca4e41fd40dd` |
| `m3/dataset_validation/huynh-nhi.json` | 1,720 | `e9df341822dbc232` |
| `m3/dataset_validation/khang.json` | 1,716 | `1fbdd9fac4a34401` |
| `m3/dataset_validation/man-nhi.json` | 1,718 | `fe4f531956c7eb6e` |
| `m3/dataset_validation/trong.json` | 1,717 | `f48409f9f807adc4` |
| `m3/experiments.json` | 14,834 | `cb8828533ad87b6b` |
| `m3/feature_analysis.md` | 3,835 | `64d76f1abdcce723` |
| `m3/feature_distributions.png` | 83,905 | `e1dce6d2728f2831` |
| `m3/forensic_errors_6_cases.json` | 6,252 | `5de35a75fcdb3939` |
| `m3/hf_dataset_cards/hoang_README.md` | 991 | `bb454a2d252077d7` |
| `m3/hf_dataset_cards/huynh-nhi_README.md` | 995 | `612630260810735a` |
| `m3/hf_dataset_cards/khang_README.md` | 991 | `9feedbbe403ef92c` |
| `m3/hf_dataset_cards/man-nhi_README.md` | 993 | `be8918b2d9ad2682` |
| `m3/hf_dataset_cards/trong_README.md` | 991 | `365fd4df92b60297` |
| `m3/label_report.json` | 3,103 | `d367c5d7078bc68d` |
| `m3/manual_labels_audit.json` | 2,216 | `b9be2279fca9ff2c` |
| `m3/metrics_baseline.json` | 3,874 | `fd5250e144128841` |
| `m3/metrics_model.json` | 3,798 | `ab511a184b6937b7` |
| `m3/model_baseline.json` | 145 | `553e7cf1ef219be9` |
| `m3/model_config.json` | 1,443 | `752ef76ffb2b350a` |
| `m3/model_logistic.json` | 2,411 | `0f81f373f07f4362` |
| `m3/run_config.json` | 31,082 | `fd842b7f82534c7e` |
| `m3/training_report.json` | 9,282 | `9b8b3287429850be` |
| `m3/validation_baseline.json` | 149 | `45385e19e9fc4629` |
| `m3/validation_model.json` | 149 | `45385e19e9fc4629` |

Total archived evidence: **46 files**, **405.0 KiB**.
