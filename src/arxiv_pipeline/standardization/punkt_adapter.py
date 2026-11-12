"""Adapter thử nghiệm Punkt, không đổi tokenizer mặc định."""

from functools import lru_cache

from .segmentation_utils import masked_view, split_at_ends


@lru_cache(maxsize=1)
def _model():
    # Load lười, không download model trong workflow.
    from nltk.tokenize.punkt import PunktTokenizer
    return PunktTokenizer("english")


def tokenize_sentences_punkt(text: str) -> list[str]:
    if not text.strip():
        return []
    view, regions = masked_view(text)
    return split_at_ends(text, (end for _, end in _model().span_tokenize(view)), regions)
