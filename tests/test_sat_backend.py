"""Smoke test backend SaT thật; chạy riêng sau khi cài dependency/model local."""

import os
from pathlib import Path

import pytest

pytest.importorskip("wtpsplit")

from arxiv_pipeline.standardization import process_prose, tokenize_sentences
from arxiv_pipeline.standardization.neural_adapter import sat_model


@pytest.fixture(autouse=True)
def local_model(monkeypatch):
    path = Path(os.environ.get("ARXIV_SAT_MODEL", "data/models/sat-3l-sm"))
    tokenizer = Path(os.environ.get("ARXIV_SAT_TOKENIZER", "data/models/xlm-roberta-base-tokenizer"))
    if not (path / "model_optimized.onnx").is_file() or not (tokenizer / "tokenizer.json").is_file():
        pytest.skip("Cần chạy standardization.download_models trước test SaT thật")
    monkeypatch.setenv("ARXIV_SENTENCE_BACKEND", "sat")


def test_optional_sat_uses_cpu_model():
    assert sat_model().model.ort_session.get_providers() == ["CPUExecutionProvider"]


def test_initials_and_country_context():
    assert tokenize_sentences("I.-M.I. was supported by the Project.") == ["I.-M.I. was supported by the Project."]
    assert tokenize_sentences("K.D. and J.L. acknowledge financial support.") == ["K.D. and J.L. acknowledge financial support."]
    assert tokenize_sentences("They worked in the U.S. Results followed.") == ["They worked in the U.S.", "Results followed."]


def test_prose_preserves_math_and_citation():
    text = r"\textbf{Our method} uses $\mathbf{x}$ and \citep[p. 15]{key}."
    sentences = process_prose(text)
    assert " ".join(sentences) == r"Our method uses $\mathbf{x}$ and \citep[p. 15]{key}."
