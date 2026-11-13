"""Test chuan hoa toan hoc (inline va display)."""

import unittest
from arxiv_pipeline.standardization import normalize_math


class TestStandardizationMath(unittest.TestCase):
    def test_inline(self):
        for raw in [r"$ x $", r"\( x \)", r"\begin{math} x \end{math}"]:
            with self.subTest(raw=raw):
                self.assertEqual(normalize_math(raw), "$x$")

    def test_display(self):
        expected = "\\begin{equation}\nx\n\\end{equation}"
        for raw in [
            "$$ x $$",
            r"\[ x \]",
            r"\begin{equation} x \end{equation}",
            r"\begin{align*} x \end{align*}",
            r"\begin{gather} x \end{gather}",
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(normalize_math(raw), expected)
                self.assertEqual(normalize_math(expected), expected)

    def test_preserves_expression_prose_and_escapes(self):
        raw = r"Cost \$5. Use \( \mathbf{x} + \text{a  b} \). See \cite{k}."
        expected = r"Cost \$5. Use $\mathbf{x} + \text{a  b}$. See \cite{k}."
        self.assertEqual(normalize_math(raw), expected)
        self.assertEqual(normalize_math(expected), expected)
        self.assertEqual(normalize_math(r"row\\[2mm] next"), r"row\\[2mm] next")

    def test_incomplete_math_is_unchanged(self):
        for raw in ["$", "$unfinished", "$$x$", r"\( x", r"$x\$", r"\begin{align}x\end{gather}"]:
            with self.subTest(raw=raw):
                self.assertEqual(normalize_math(raw), raw)

    def test_multiple_math_spans(self):
        self.assertEqual(normalize_math(r"A \( x \), then $ y $."), "A $x$, then $y$.")


if __name__ == "__main__":
    unittest.main()
