"""Test tùy chọn cho adapter Punkt; không ép pipeline mặc định cài NLTK."""

import unittest

try:
    import nltk
    from arxiv_pipeline.standardization.punkt_adapter import _model, tokenize_sentences_punkt
    _model()
    HAS_PUNKT = True
except Exception:
    HAS_PUNKT = False


@unittest.skipIf(not HAS_PUNKT, "Cần tải punkt_tab và cấu hình NLTK_DATA để test adapter")
class TestPunktAdapter(unittest.TestCase):
    def test_protected_region_preserved(self):
        regions = [
            r"$\text{a.  b} = 3.14$",
            r"\citep[see {Sec. 2. More}]{a.b}",
            r"\begin{equation}a. b\end{equation}",
            "https://example.org/a.b",
            "a.b@example.org",
            r"e.\,g. movement",
        ]
        for region in regions:
            with self.subTest(region=region):
                first = f"Use {region}."
                self.assertEqual(tokenize_sentences_punkt(first + " Next."), [first, "Next."])

    def test_country_abbreviation_context(self):
        self.assertEqual(
            tokenize_sentences_punkt("The U.S. Department agreed. Next."),
            ["The U.S. Department agreed.", "Next."],
        )
        self.assertEqual(
            tokenize_sentences_punkt("They worked in the U.S. Results followed."),
            ["They worked in the U.S.", "Results followed."],
        )

    def test_empty_and_period_only_contract(self):
        self.assertEqual(tokenize_sentences_punkt("  "), [])
        self.assertEqual(
            tokenize_sentences_punkt("Why? Yes! Done. Next."),
            ["Why? Yes! Done.", "Next."],
        )

    def test_reference_suffix_stays_attached(self):
        first = r"Under Assumption~\ref{asu:f}.(ii), the result holds."
        self.assertEqual(tokenize_sentences_punkt(first + " Next."), [first, "Next."])


if __name__ == "__main__":
    unittest.main()
