"""Smoke test backend SaT thật; chạy riêng sau khi cài dependency/model local."""

import os
import unittest
from pathlib import Path

try:
    import wtpsplit
    path = Path(os.environ.get("ARXIV_SAT_MODEL", "data/models/sat-3l-sm"))
    tokenizer = Path(os.environ.get("ARXIV_SAT_TOKENIZER", "data/models/xlm-roberta-base-tokenizer"))
    HAS_SAT_MODEL = (path / "model_optimized.onnx").is_file() and (tokenizer / "tokenizer.json").is_file()
except ImportError:
    HAS_SAT_MODEL = False

from arxiv_pipeline.standardization import process_prose, tokenize_sentences


@unittest.skipIf(not HAS_SAT_MODEL, "Cần chạy standardization.download_models trước test SaT thật")
class TestSatBackend(unittest.TestCase):
    def setUp(self):
        self.old_env = os.environ.pop("ARXIV_SENTENCE_BACKEND", None)

    def tearDown(self):
        if self.old_env is not None:
            os.environ["ARXIV_SENTENCE_BACKEND"] = self.old_env

    def test_default_uses_cpu_model(self):
        from arxiv_pipeline.standardization.neural_adapter import sat_model
        self.assertEqual(sat_model().model.ort_session.get_providers(), ["CPUExecutionProvider"])

    def test_initials_and_country_context(self):
        self.assertEqual(
            tokenize_sentences("I.-M.I. was supported by the Project.", backend="sat"),
            ["I.-M.I. was supported by the Project."],
        )
        self.assertEqual(
            tokenize_sentences("K.D. and J.L. acknowledge financial support.", backend="sat"),
            ["K.D. and J.L. acknowledge financial support."],
        )
        self.assertEqual(
            tokenize_sentences("They worked in the U.S. Results followed.", backend="sat"),
            ["They worked in the U.S.", "Results followed."],
        )

    def test_prose_preserves_math_and_citation(self):
        text = r"\textbf{Our method} uses $\mathbf{x}$ and \citep[p. 15]{key}."
        sentences = process_prose(text, custom_tokenizer=lambda t: tokenize_sentences(t, backend="sat"))
        self.assertEqual(" ".join(sentences), r"Our method uses $\mathbf{x}$ and \citep[p. 15]{key}.")


if __name__ == "__main__":
    unittest.main()
