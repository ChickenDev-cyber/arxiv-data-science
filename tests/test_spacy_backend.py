"""Smoke test spaCy mặc định và bảo vệ dữ liệu LaTeX trên model thật."""

import pytest

pytest.importorskip("spacy")

from arxiv_pipeline.standardization import process_prose, tokenize_sentences
from arxiv_pipeline.standardization.neural_adapter import spacy_model


@pytest.fixture(autouse=True)
def local_model(monkeypatch):
    import spacy.util

    if not spacy.util.is_package("en_core_web_sm"):
        pytest.skip("Cần chạy standardization.download_models trước test spaCy thật")
    monkeypatch.delenv("ARXIV_SENTENCE_BACKEND", raising=False)


def test_default_runs_only_senter():
    assert spacy_model().pipe_names == ["senter"]


def test_initials_stay_in_sentence():
    text = "I.-M.I. was supported by the Project."
    assert tokenize_sentences(text) == [text]
    text = "K.D. and J.L. acknowledge financial support."
    assert tokenize_sentences(text) == [text]


def test_clean_math_and_citations_are_preserved():
    text = r"\textbf{Our method} uses $\mathbf{x}$ and \citep[p. 15]{key}."
    assert " ".join(process_prose(text)) == r"Our method uses $\mathbf{x}$ and \citep[p. 15]{key}."
