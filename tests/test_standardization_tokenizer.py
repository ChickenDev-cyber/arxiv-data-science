"""Các ca tách nhầm câu cần được bảo vệ trong prose LaTeX."""

import pytest

from arxiv_pipeline.standardization import tokenize_sentences


@pytest.mark.parametrize("protected", [
    r"$x = 3.14. y = 2$",
    r"\(x. y\)",
    r"\begin{equation}x. y\end{equation}",
    r"\citep[see {Sec. 2. More}][p. 15]{a.b}",
    r"\ref{sec.1}",
    "i.e. an example", "e.g. a case", "Smith et al. agree",
    "Fig. 2", "Eq. 1", "Dr. Smith", "Prof. Smith", "A vs. B",
    "cf. above", "vol. 1", "no. 2", "3.14 and 0.001",
    "https://example.org/a.b", "www.example.org/path",
    "a.b@example.org",
])
def test_protected_regions(protected):
    first = f"Use {protected}."
    assert tokenize_sentences(first + " Next sentence.") == [first, "Next sentence."]


def test_preserves_whitespace_and_literal_placeholder_in_math_and_commands():
    first = r"Use $\text{a  b}$ and \citep[p.  15]{key}."
    assert tokenize_sentences(first + " __TOKEN_MASK_0__.") == [first, "__TOKEN_MASK_0__."]


def test_escaped_dollar_is_not_math():
    assert tokenize_sentences(r"Cost \$5. Next.") == [r"Cost \$5.", "Next."]


def test_url_inside_parentheses_and_balanced_url():
    first = "See (https://example.org/a.b)."
    second = "Read https://example.org/Function_(math)."
    assert tokenize_sentences(first + " " + second + " Next.") == [first, second, "Next."]


def test_quotes_ellipsis_and_unfinished_tail():
    assert tokenize_sentences('He said "Done." Wait... Last fragment') == [
        'He said "Done."', "Wait...", "Last fragment",
    ]


def test_only_periods_create_boundaries():
    assert tokenize_sentences("Why? Yes! Done. Next.") == ["Why? Yes! Done.", "Next."]


@pytest.mark.parametrize("text", ["", " \n\t "])
def test_empty(text):
    assert tokenize_sentences(text) == []


@pytest.mark.parametrize("abbreviation", [
    "eg.", "EG.", "e.g.", "e. g.", r"e.\,g.", r"e.\ g.",
    r"i.\,e.", r"e.\;g.", "e.~g.", r"et\,al.",
])
def test_vocab_supports_spacing_without_changing_source(abbreviation):
    first = f"Use {abbreviation} natural movement."
    assert tokenize_sentences(first + " Next.") == [first, "Next."]


def test_does_not_protect_unknown_words_or_arbitrary_commands():
    assert tokenize_sentences("A meg. Next.") == ["A meg.", "Next."]
    assert tokenize_sentences(r"Use e. \unknown{g.} Next.") == ["Use e.", r"\unknown{g.} Next."]
    assert tokenize_sentences("Done. Next.") == ["Done.", "Next."]


@pytest.mark.parametrize("tail", [r"$x. incomplete", r"\citep[see {Sec. 2. incomplete"])
def test_incomplete_regions_preserve_tail(tail):
    assert tokenize_sentences("First. " + tail) == ["First.", tail]
