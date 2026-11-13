"""Các ca tách nhầm câu cần được bảo vệ trong prose LaTeX."""

import unittest
from arxiv_pipeline.standardization import tokenize_sentences


class TestStandardizationTokenizer(unittest.TestCase):
    def test_protected_regions(self):
        protected_cases = [
            r"$x = 3.14. y = 2$",
            r"\(x. y\)",
            r"\begin{equation}x. y\end{equation}",
            r"\citep[see {Sec. 2. More}][p. 15]{a.b}",
            r"\ref{sec.1}",
            "i.e. an example",
            "e.g. a case",
            "Smith et al. agree",
            "Fig. 2",
            "Eq. 1",
            "Dr. Smith",
            "Prof. Smith",
            "A vs. B",
            "cf. above",
            "vol. 1",
            "no. 2",
            "3.14 and 0.001",
            "https://example.org/a.b",
            "www.example.org/path",
            "a.b@example.org",
        ]
        for protected in protected_cases:
            with self.subTest(protected=protected):
                first = f"Use {protected}."
                self.assertEqual(
                    tokenize_sentences(first + " Next sentence."),
                    [first, "Next sentence."],
                )

    def test_preserves_whitespace_and_literal_placeholder_in_math_and_commands(self):
        first = r"Use $\text{a  b}$ and \citep[p.  15]{key}."
        self.assertEqual(
            tokenize_sentences(first + " __TOKEN_MASK_0__."),
            [first, "__TOKEN_MASK_0__."],
        )

    def test_escaped_dollar_is_not_math(self):
        self.assertEqual(
            tokenize_sentences(r"Cost \$5. Next."),
            [r"Cost \$5.", "Next."],
        )

    def test_url_inside_parentheses_and_balanced_url(self):
        first = "See (https://example.org/a.b)."
        second = "Read https://example.org/Function_(math)."
        self.assertEqual(
            tokenize_sentences(first + " " + second + " Next."),
            [first, second, "Next."],
        )

    def test_quotes_ellipsis_and_unfinished_tail(self):
        self.assertEqual(
            tokenize_sentences('He said "Done." Wait... Last fragment'),
            ['He said "Done."', "Wait...", "Last fragment"],
        )

    def test_only_periods_create_boundaries(self):
        self.assertEqual(
            tokenize_sentences("Why? Yes! Done. Next."),
            ["Why? Yes! Done.", "Next."],
        )

    def test_empty(self):
        for text in ["", " \n\t "]:
            with self.subTest(text=text):
                self.assertEqual(tokenize_sentences(text), [])

    def test_vocab_supports_spacing_without_changing_source(self):
        abbreviations = [
            "eg.", "EG.", "e.g.", "e. g.", r"e.\,g.", r"e.\ g.",
            r"i.\,e.", r"e.\;g.", "e.~g.", r"et\,al.",
        ]
        for abbr in abbreviations:
            with self.subTest(abbr=abbr):
                first = f"Use {abbr} natural movement."
                self.assertEqual(
                    tokenize_sentences(first + " Next."),
                    [first, "Next."],
                )

    def test_does_not_protect_unknown_words_or_arbitrary_commands(self):
        self.assertEqual(tokenize_sentences("A meg. Next."), ["A meg.", "Next."])
        self.assertEqual(tokenize_sentences(r"Use e. \unknown{g.} Next."), ["Use e.", r"\unknown{g.} Next."])
        self.assertEqual(tokenize_sentences("Done. Next."), ["Done.", "Next."])

    def test_incomplete_regions_preserve_tail(self):
        for tail in [r"$x. incomplete", r"\citep[see {Sec. 2. incomplete"]:
            with self.subTest(tail=tail):
                self.assertEqual(tokenize_sentences("First. " + tail), ["First.", tail])


if __name__ == "__main__":
    unittest.main()
