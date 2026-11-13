"""API mac dinh rule va cac lua chon baseline ro rang, khong can model."""

import os
import unittest
from arxiv_pipeline.standardization import clean_latex, tokenize_sentences
from arxiv_pipeline.standardization import cleaner, neural_adapter


class TestTokenizerBackend(unittest.TestCase):
    def test_default_routes_to_rule_without_model(self):
        """Default routing without env var must run rule backend and require no model."""
        old_env = os.environ.pop("ARXIV_SENTENCE_BACKEND", None)
        try:
            result = tokenize_sentences("First sentence. Second sentence.")
            self.assertEqual(result, ["First sentence.", "Second sentence."])
        finally:
            if old_env is not None:
                os.environ["ARXIV_SENTENCE_BACKEND"] = old_env

    def test_rule_is_explicit_and_does_not_need_model(self):
        result = tokenize_sentences("First. Next.", backend="rule")
        self.assertEqual(result, ["First.", "Next."])

    def test_invalid_backend_is_not_silent_fallback(self):
        with self.assertRaises(ValueError):
            tokenize_sentences("Input.", backend="unknown_backend_xyz")

    def test_assignment_cleanup_name(self):
        self.assertIs(clean_latex, cleaner.clean_latex)
        self.assertEqual(clean_latex.__name__, "clean_latex")
        self.assertEqual(clean_latex(r"\textbf{Result}"), "Result")


if __name__ == "__main__":
    unittest.main()
