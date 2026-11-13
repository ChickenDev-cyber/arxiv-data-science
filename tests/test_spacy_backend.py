"""Smoke test spaCy và bảo vệ dữ liệu LaTeX trên model thật."""

import os
import unittest

try:
    import spacy
    import spacy.util
    HAS_SPACY_MODEL = spacy.util.is_package("en_core_web_sm")
except ImportError:
    HAS_SPACY_MODEL = False

from arxiv_pipeline.standardization import process_prose, tokenize_sentences


@unittest.skipIf(not HAS_SPACY_MODEL, "Cần cài đặt spacy và model en_core_web_sm")
class TestSpacyBackend(unittest.TestCase):
    def setUp(self):
        self.old_env = os.environ.pop("ARXIV_SENTENCE_BACKEND", None)

    def tearDown(self):
        if self.old_env is not None:
            os.environ["ARXIV_SENTENCE_BACKEND"] = self.old_env

    def test_runs_only_senter(self):
        from arxiv_pipeline.standardization.neural_adapter import spacy_model
        self.assertEqual(spacy_model().pipe_names, ["senter"])

    def test_initials_stay_in_sentence(self):
        text1 = "I.-M.I. was supported by the Project."
        self.assertEqual(tokenize_sentences(text1, backend="spacy"), [text1])
        text2 = "K.D. and J.L. acknowledge financial support."
        self.assertEqual(tokenize_sentences(text2, backend="spacy"), [text2])

    def test_clean_math_and_citations_are_preserved(self):
        text = r"\textbf{Our method} uses $\mathbf{x}$ and \citep[p. 15]{key}."
        sentences = process_prose(text, custom_tokenizer=lambda t: tokenize_sentences(t, backend="spacy"))
        self.assertEqual(" ".join(sentences), r"Our method uses $\mathbf{x}$ and \citep[p. 15]{key}.")


if __name__ == "__main__":
    unittest.main()
