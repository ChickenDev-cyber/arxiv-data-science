"""Kiểm tra thứ tự và kết quả ghép ba bước xử lý prose."""

from arxiv_pipeline.standardization import process_prose


def test_process_prose_unwraps_before_splitting_and_preserves_math():
    text = (
        r"\textbf{First sentence. Second uses \(\mathbf{x} = 3.14\).} "
        r"\newpage See Fig. 2 and \citep[p. 15]{canonical}."
    )
    assert process_prose(text) == [
        "First sentence.",
        r"Second uses $\mathbf{x} = 3.14$.",
        r"See Fig. 2 and \citep[p. 15]{canonical}.",
    ]


def test_process_prose_empty():
    assert process_prose(" \n ") == []


def test_custom_tokenizer_receives_cleaned_normalized_text():
    received = []

    def custom(text):
        received.append(text)
        return [text]

    assert process_prose(r"\emph{Use} \( x \).", custom_tokenizer=custom) == ["Use $x$."]
    assert received == ["Use $x$."]
