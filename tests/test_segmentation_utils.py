"""Kiểm tra ánh xạ vị trí dùng chung, độc lập với model NLP tùy chọn."""

from arxiv_pipeline.standardization.segmentation_utils import masked_view, split_at_ends


def test_model_boundary_inside_nested_citation_is_rejected():
    text = r"See \citep[see {Sec. 2. More}]{a.b}. Next."
    view, regions = masked_view(text)
    assert len(view) == len(text)
    assert split_at_ends(text, [text.index("2.") + 2, text.index("Next") - 1, len(text)], regions) == [
        r"See \citep[see {Sec. 2. More}]{a.b}.", "Next.",
    ]


def test_original_math_whitespace_and_literal_x_are_preserved():
    text = r"Use $\text{a.  b}$. xxxxx."
    _, regions = masked_view(text)
    end = text.index(" xxxxx")
    assert split_at_ends(text, [end, len(text)], regions) == [r"Use $\text{a.  b}$.", "xxxxx."]


def test_closing_quote_attaches_to_previous_sentence():
    text = 'He said "Done." Next.'
    _, regions = masked_view(text)
    assert split_at_ends(text, [text.index('Done.') + 5, len(text)], regions) == ['He said "Done."', "Next."]


def test_question_boundary_does_not_change_period_only_contract():
    text = "Why? Done. Next."
    _, regions = masked_view(text)
    assert split_at_ends(text, [4, 10, len(text)], regions) == ["Why? Done.", "Next."]
