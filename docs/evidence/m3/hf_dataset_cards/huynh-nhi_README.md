---
configs:
- config_name: papers
  data_files:
  - split: train
    path: data/papers/*.parquet
- config_name: references
  data_files:
  - split: train
    path: data/references/*.parquet
- config_name: elements
  data_files:
  - split: train
    path: data/elements/*.parquet
- config_name: hierarchy
  data_files:
  - split: train
    path: data/hierarchy/*.parquet
- config_name: bibtex
  data_files:
  - split: train
    path: data/bibtex/*.parquet
- config_name: predictions
  data_files:
  - split: train
    path: data/predictions/*.parquet
---

# arXiv M1–M3 dataset — huynh-nhi

Six explicit Parquet tables. M1/M2 tables cover this owner’s corpus; predictions cover only matching-pool papers. The partition column is the ML split; the Hub storage split named train is independent.

Candidates come from the citing paper’s references. Unknown ground truth is null; internal labels distinguish unmatched from unreviewed entries. Only valid matched entries contribute to MRR@5.
