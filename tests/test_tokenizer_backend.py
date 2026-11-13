"""API mặc định spaCy và lựa chọn baseline phải rõ ràng, không fallback ngầm."""

import pytest

from arxiv_pipeline.standardization import clean_latex, tokenize_sentences
from arxiv_pipeline.standardization import cleaner, neural_adapter


def test_default_routes_to_spacy(monkeypatch):
    monkeypatch.delenv("ARXIV_SENTENCE_BACKEND", raising=False)
    received = []

    def fake(text):
        received.append(text)
        return [text]

    monkeypatch.setattr(neural_adapter, "tokenize_sentences_spacy", fake)
    assert tokenize_sentences("Input.") == ["Input."]
    assert received == ["Input."]


def test_rule_is_explicit_and_does_not_need_model(monkeypatch):
    monkeypatch.setenv("ARXIV_SENTENCE_BACKEND", "sat")
    assert tokenize_sentences("First. Next.", backend="rule") == ["First.", "Next."]


def test_invalid_backend_is_not_silent_fallback():
    with pytest.raises(ValueError, match="Unknown sentence backend"):
        tokenize_sentences("Input.", backend="typo")


def test_missing_spacy_model_does_not_fall_back(monkeypatch):
    def missing(text):
        raise RuntimeError("Missing spaCy model")
    monkeypatch.setattr(neural_adapter, "tokenize_sentences_spacy", missing)
    with pytest.raises(RuntimeError, match="Missing spaCy model"):
        tokenize_sentences("Input.", backend="spacy")


def test_assignment_cleanup_name():
    assert clean_latex is cleaner.clean_latex
    assert clean_latex.__name__ == "clean_latex"
    assert clean_latex(r"\textbf{Result}") == "Result"
