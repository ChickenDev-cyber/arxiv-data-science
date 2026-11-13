import pytest

from arxiv_pipeline.standardization import normalize_math


@pytest.mark.parametrize("raw", [r"$ x $", r"\( x \)", r"\begin{math} x \end{math}"])
def test_inline(raw):
    assert normalize_math(raw) == "$x$"


@pytest.mark.parametrize("raw", [
    "$$ x $$", r"\[ x \]", r"\begin{equation} x \end{equation}",
    r"\begin{align*} x \end{align*}", r"\begin{gather} x \end{gather}",
])
def test_display(raw):
    expected = "\\begin{equation}\nx\n\\end{equation}"
    assert normalize_math(raw) == expected
    assert normalize_math(expected) == expected


def test_preserves_expression_prose_and_escapes():
    raw = r"Cost \$5. Use \( \mathbf{x} + \text{a  b} \). See \cite{k}."
    expected = r"Cost \$5. Use $\mathbf{x} + \text{a  b}$. See \cite{k}."
    assert normalize_math(raw) == expected
    assert normalize_math(expected) == expected
    assert normalize_math(r"row\\[2mm] next") == r"row\\[2mm] next"


@pytest.mark.parametrize("raw", ["$", "$unfinished", "$$x$", r"\( x", r"$x\$", r"\begin{align}x\end{gather}"])
def test_incomplete_math_is_unchanged(raw):
    assert normalize_math(raw) == raw


def test_multiple_math_spans():
    assert normalize_math(r"A \( x \), then $ y $.") == "A $x$, then $y$."
