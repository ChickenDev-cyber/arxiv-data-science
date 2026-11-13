"""Adapter spaCy mặc định và SaT đối chiếu; bảo toàn nội dung gốc."""

import os
from functools import lru_cache
from pathlib import Path

from .segmentation_utils import masked_view, split_at_ends


@lru_cache(maxsize=1)
def spacy_model():
    try:
        import spacy
    except ImportError as exc:
        raise RuntimeError("spaCy requires: python -m pip install -r src/requirements-spacy.txt") from exc

    spacy.require_cpu()
    # Chỉ bật senter: không dùng NER, parser, tagger hoặc lemmatizer.
    # Model tắt senter sẵn trong config; bật sau khi load để tránh xung đột enable.
    try:
        nlp = spacy.load("en_core_web_sm", disable=[
            "tok2vec", "tagger", "parser", "attribute_ruler", "lemmatizer", "ner",
        ])
    except OSError as exc:
        raise RuntimeError("Missing spaCy model. Run: PYTHONPATH=src python -m arxiv_pipeline.standardization.download_models") from exc
    nlp.enable_pipe("senter")
    assert nlp.pipe_names == ["senter"]
    return nlp


def tokenize_sentences_spacy(text: str) -> list[str]:
    if not text.strip():
        return []
    view, regions = masked_view(text)
    doc = spacy_model()(view)
    return split_at_ends(text, (sentence.end_char for sentence in doc.sents), regions)


@lru_cache(maxsize=1)
def sat_model():
    try:
        from wtpsplit import SaT
        import onnxruntime as ort
    except ImportError as exc:
        raise RuntimeError("SaT requires: python -m pip install -r src/requirements-sat.txt") from exc

    # Model tải trước; không tự phát sinh network request trong workflow.
    path = Path(os.environ.get("ARXIV_SAT_MODEL", "data/models/sat-3l-sm")).resolve()
    tokenizer_path = Path(os.environ.get("ARXIV_SAT_TOKENIZER", "data/models/xlm-roberta-base-tokenizer")).resolve()
    if not (path / "model_optimized.onnx").is_file() or not (path / "config.json").is_file() or not (tokenizer_path / "tokenizer.json").is_file():
        raise RuntimeError("Missing local SaT model/tokenizer. Run: PYTHONPATH=src python -m arxiv_pipeline.standardization.download_models --backend sat")
    options = ort.SessionOptions()
    # Cố định số thread để các đoạn ngắn không tạo overhead từ quá nhiều core.
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    return SaT(str(path), tokenizer_name_or_path=str(tokenizer_path),
               ort_providers=["CPUExecutionProvider"], hub_prefix=None,
               ort_kwargs={"sess_options": options},
               from_pretrained_kwargs={"local_files_only": True})


def tokenize_sentences_sat(text: str) -> list[str]:
    if not text.strip():
        return []
    view, regions = masked_view(text)
    segments = sat_model().split(view)
    ends = []
    offset = 0
    for segment in segments:
        # Chỉ tìm từ offset trước, tránh ánh xạ sai khi cùng câu xuất hiện nhiều lần.
        start = view.find(segment, offset)
        if start == -1 or view[offset:start].strip():
            raise ValueError("SaT output cannot be aligned to the original input")
        offset = start + len(segment)
        end = offset
        while end > start and view[end - 1].isspace():
            end -= 1
        ends.append(end)
    if view[offset:].strip():
        raise ValueError("SaT omitted input content")
    return split_at_ends(text, ends, regions)
