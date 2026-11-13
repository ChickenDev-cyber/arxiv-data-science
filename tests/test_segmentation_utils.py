"""Kiểm tra ánh xạ vị trí dùng chung, độc lập với model NLP tùy chọn."""

import unittest
from arxiv_pipeline.standardization.segmentation_utils import masked_view, split_at_ends


class TestSegmentationUtils(unittest.TestCase):
    def test_model_boundary_inside_nested_citation_is_rejected(self):
        text = r"See \citep[see {Sec. 2. More}]{a.b}. Next."
        view, regions = masked_view(text)
        self.assertEqual(len(view), len(text))
        self.assertEqual(
            split_at_ends(text, [text.index("2.") + 2, text.index("Next") - 1, len(text)], regions),
            [r"See \citep[see {Sec. 2. More}]{a.b}.", "Next."],
        )

    def test_original_math_whitespace_and_literal_x_are_preserved(self):
        text = r"Use $\text{a.  b}$. xxxxx."
        _, regions = masked_view(text)
        end = text.index(" xxxxx")
        self.assertEqual(split_at_ends(text, [end, len(text)], regions), [r"Use $\text{a.  b}$.", "xxxxx."])

    def test_closing_quote_attaches_to_previous_sentence(self):
        text = 'He said "Done." Next.'
        _, regions = masked_view(text)
        self.assertEqual(
            split_at_ends(text, [text.index('Done.') + 5, len(text)], regions),
            ['He said "Done."', "Next."],
        )

    def test_question_boundary_does_not_change_period_only_contract(self):
        text = "Why? Done. Next."
        _, regions = masked_view(text)
        self.assertEqual(split_at_ends(text, [4, 10, len(text)], regions), ["Why? Done.", "Next."])


if __name__ == "__main__":
    unittest.main()
