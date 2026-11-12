"""API tách câu Module 2; mặc định SaT theo quyết định triển khai của nhóm."""

import os


def tokenize_sentences(text: str, *, backend: str | None = None) -> list[str]:
    """Nhận prose đã chuẩn hóa, trả các câu cắt từ chuỗi gốc.

    SaT chạy CPU với model local. Có thể chọn rule để chạy regression baseline.
    Không âm thầm fallback khi thiếu dependency/model vì sẽ làm output thay đổi.
    """
    selected = backend if backend is not None else os.environ.get("ARXIV_SENTENCE_BACKEND", "sat")
    if selected not in {"sat", "rule", "spacy", "punkt"}:
        raise ValueError(f"Unknown sentence backend: {selected!r}")
    if not text.strip():
        return []
    # Import lười: cleaner/math và backend rule không cần tải thư viện NLP.
    if selected == "rule":
        from .rule_tokenizer import tokenize_sentences as tokenize
    elif selected == "punkt":
        from .punkt_adapter import tokenize_sentences_punkt as tokenize
    elif selected == "spacy":
        from .neural_adapter import tokenize_sentences_spacy as tokenize
    else:
        from .neural_adapter import tokenize_sentences_sat as tokenize
    return tokenize(text)
