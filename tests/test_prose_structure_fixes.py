"""Tái hiện lỗi abstract, caption, proof/label và vùng comment trên corpus."""

import pytest

from arxiv_pipeline.hierarchy import build_hierarchy
from arxiv_pipeline.hierarchy.latex_utils import strip_comment_environments, strip_prose_markup
from arxiv_pipeline.standardization import clean_text


def parse(body):
    return build_hierarchy({
        "arxiv_id": "2506.20794", "version": 1, "status": "ok",
        "root_file": "main.tex", "compilation_path": ["main.tex"],
        "bib_files": [], "bbl_files": [],
        "merged_tex": r"\documentclass{article}\begin{document}" + body + r"\end{document}",
    })["nodes"]


def prose(nodes):
    return [n["content"] for n in nodes if n["kind"] == "sentence"]


def test_comment_removed_before_structure_scan():
    nodes = parse(r"Before.\begin{comment}\section{Hidden}\item Secret.\end{comment}After.")
    assert prose(nodes) == ["Before.", "After."]
    assert not any(n["kind"] in {"section", "item"} for n in nodes)


def test_repeated_visible_text_survives_comment_removal():
    # Cùng một câu có thể tồn tại cả ngoài và trong comment: chỉ bỏ bản bị ẩn.
    nodes = parse(r"Visible text.\begin{comment}Visible text.\[hidden=1\]\end{comment}")
    assert prose(nodes) == ["Visible text."]
    assert not any(n["kind"] == "formula" for n in nodes)


def test_unclosed_comment_reports_and_omits_tail(caplog):
    assert prose(parse(r"Before.\begin{comment}Secret.")) == ["Before."]
    assert "Unclosed comment" in caplog.text


def test_comment_examples_inside_literal_are_preserved():
    raw = r"\verb|\begin{comment}| \begin{verbatim}\begin{comment}\end{verbatim}"
    assert strip_comment_environments(raw) == raw
    assert strip_comment_environments(r"A\\begin{comment}B") == r"A\\begin{comment}B"


def test_commented_out_comment_opener_is_not_active():
    assert prose(parse("Before.\n% \\begin{comment}\nAfter.")) == ["Before.", "After."]


@pytest.mark.parametrize("body", [
    r"\abstract{First. \textbf{Second.}}",
    r"\abstract{First.}{Second.}{}{}{Third.}",
    r"\begin{abstract}First. Second.\end{abstract}",
])
def test_abstract_prose_is_split(body):
    expected = ["First.", "Second."]
    if "Third." in body:
        expected.append("Third.")
    assert prose(parse(body)) == expected


def test_abstract_preserves_math_structure_and_citations():
    nodes = parse(r"\abstract{Use $\mathbf{x}$ and \cite{k}.\[x=1\]Next.}\section{Body}Done.")
    assert prose(nodes) == [r"Use $\mathbf{x}$ and \cite{k}.", "Next.", "Done."]
    assert sum(n["kind"] == "formula" for n in nodes) == 1
    assert sum(n["kind"] == "section" for n in nodes) == 1


def test_caption_cleaned_without_splitting_figure():
    raw = r"\begin{figure}\caption*[\emph{Short}]{\textbf{Main \emph{result}.} Use $\mathbf{x}$ and \cite{k}.}\end{figure}"
    cleaned = clean_text(raw)
    assert r"\caption*[Short]{Main result. Use $\mathbf{x}$ and \cite{k}.}" in cleaned
    assert clean_text(cleaned) == cleaned
    nodes = parse(raw)
    assert not prose(nodes)
    assert sum(n["kind"] == "figure" for n in nodes) == 1


def test_unknown_command_and_malformed_caption_preserved():
    for raw in [r"\unknown{\textbf{Keep}}", r"\caption[unfinished", r"\caption{unfinished"]:
        assert clean_text(raw) == raw


def test_proof_and_label_removed_without_breaking_sentence():
    nodes = parse(r"\begin{proof}[Proof of Lemma 2]The\label{p} result follows.\end{proof} Next.")
    assert prose(nodes) == ["The result follows.", "Next."]


def test_semantic_commands_and_math_labels_preserved():
    raw = r"Use $x\label{eq}$ and \ref{eq} and \cite{k}. \unknown{\label{keep}}"
    assert strip_prose_markup(raw) == raw


def test_algorithm_and_unknown_environments_are_not_deleted():
    raw = r"\begin{algorithm}\State Work.\end{algorithm}\begin{custom}Text.\end{custom}"
    assert strip_prose_markup(raw) == raw
