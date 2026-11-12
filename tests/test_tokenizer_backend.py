"""API mặc định SaT và lựa chọn baseline phải rõ ràng, không fallback ngầm."""

import pytest

from arxiv_pipeline.standardization import clean_latex, clean_text, tokenize_sentences
from arxiv_pipeline.standardization import neural_adapter


def test_default_routes_to_sat(monkeypatch):
    monkeypatch.delenv("ARXIV_SENTENCE_BACKEND", raising=False)
    received = []

    def fake(text):
        received.append(text)
        return [text]

    monkeypatch.setattr(neural_adapter, "tokenize_sentences_sat", fake)
    assert tokenize_sentences("Input.") == ["Input."]
    assert received == ["Input."]


def test_rule_is_explicit_and_does_not_need_model(monkeypatch):
    monkeypatch.setenv("ARXIV_SENTENCE_BACKEND", "sat")
    assert tokenize_sentences("First. Next.", backend="rule") == ["First.", "Next."]


def test_invalid_backend_is_not_silent_fallback():
    with pytest.raises(ValueError, match="Unknown sentence backend"):
        tokenize_sentences("Input.", backend="typo")


def test_assignment_cleanup_name():
    assert clean_latex is clean_text
    assert clean_latex(r"\textbf{Result}") == "Result"
