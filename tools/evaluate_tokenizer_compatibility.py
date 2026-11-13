"""Chạy nguyên kỳ vọng test tokenizer với backend thử nghiệm, không sửa tests."""

import argparse

import pytest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("backend", choices=["punkt", "spacy", "sat"])
    backend = parser.parse_args().backend
    if backend == "punkt":
        from arxiv_pipeline.standardization.punkt_adapter import tokenize_sentences_punkt
        tokenize = tokenize_sentences_punkt
    else:
        from arxiv_pipeline.standardization.neural_adapter import tokenize_sentences_spacy, tokenize_sentences_sat
        tokenize = tokenize_sentences_spacy if backend == "spacy" else tokenize_sentences_sat

    class UseBackend:
        def pytest_collection_modifyitems(self, items):
            for item in items:
                if item.module.__name__.endswith("test_standardization_tokenizer"):
                    item.module.tokenize_sentences = tokenize

    return pytest.main(["-q", "-p", "no:cacheprovider", "tests/test_standardization_tokenizer.py"], plugins=[UseBackend()])


if __name__ == "__main__":
    raise SystemExit(main())
