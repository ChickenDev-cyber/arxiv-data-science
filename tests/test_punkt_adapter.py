"""Test tùy chọn cho adapter Punkt; không ép pipeline mặc định cài NLTK."""

import pytest

pytest.importorskip("nltk")

from arxiv_pipeline.standardization.punkt_adapter import _model, tokenize_sentences_punkt


@pytest.fixture(autouse=True)
def punkt_model():
    try:
        _model()
    except LookupError:
        pytest.skip("Cần tải punkt_tab và cấu hình NLTK_DATA để test adapter")


@pytest.mark.parametrize("region", [
    r"$\text{a.  b} = 3.14$", r"\citep[see {Sec. 2. More}]{a.b}",
    r"\begin{equation}a. b\end{equation}", "https://example.org/a.b", "a.b@example.org",
    r"e.\,g. movement",
])
def test_protected_region_preserved(region):
    first = f"Use {region}."
    assert tokenize_sentences_punkt(first + " Next.") == [first, "Next."]


def test_country_abbreviation_context():
    assert tokenize_sentences_punkt("The U.S. Department agreed. Next.") == ["The U.S. Department agreed.", "Next."]
    assert tokenize_sentences_punkt("They worked in the U.S. Results followed.") == ["They worked in the U.S.", "Results followed."]


def test_empty_and_period_only_contract():
    assert tokenize_sentences_punkt("  ") == []
    assert tokenize_sentences_punkt("Why? Yes! Done. Next.") == ["Why? Yes! Done.", "Next."]


def test_reference_suffix_stays_attached():
    first = r"Under Assumption~\ref{asu:f}.(ii), the result holds."
    assert tokenize_sentences_punkt(first + " Next.") == [first, "Next."]
