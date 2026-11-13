"""So sánh spaCy/SaT với baseline trên cùng dữ liệu đã lưu."""

import json
import time
from collections import Counter
from pathlib import Path

from arxiv_pipeline.hierarchy import build_hierarchy
from arxiv_pipeline.standardization import clean_latex, normalize_math
from arxiv_pipeline.standardization.rule_tokenizer import tokenize_sentences
import argparse
from arxiv_pipeline.standardization.neural_adapter import (
    spacy_model, sat_model, tokenize_sentences_spacy, tokenize_sentences_sat,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("backend", choices=["spacy", "sat"])
    backend = parser.parse_args().backend
    _model, tokenize_candidate = ((spacy_model, tokenize_sentences_spacy) if backend == "spacy"
                                  else (sat_model, tokenize_sentences_sat))
    base = Path("data/module2-khang-review")
    output = base / f"{backend}-experiment"
    output.mkdir(parents=True, exist_ok=True)
    _model()  # Không trộn thời gian load model vào phép đo tokenize bên dưới.
    cases = [
        ("initials-hyphen", "I.-M.I. was supported by the Project.", ["I.-M.I. was supported by the Project."]),
        ("initials-list", "K.D. and J.L. acknowledge financial support.", ["K.D. and J.L. acknowledge financial support."]),
        ("initials-single", "L.B. acknowledges support.", ["L.B. acknowledges support."]),
        ("initials-unseen", "R.T. and Z.W. contributed equally.", ["R.T. and Z.W. contributed equally."]),
        ("country-continues", "This research is funded by the U.S. Department of Energy.", ["This research is funded by the U.S. Department of Energy."]),
        ("country-ends", "They worked in the U.S. Results followed.", ["They worked in the U.S.", "Results followed."]),
        ("initials-ends", "We contacted K.D. Results followed.", ["We contacted K.D.", "Results followed."]),
        ("decimal", "Accuracy is 3.14. Results follow.", ["Accuracy is 3.14.", "Results follow."]),
        ("math", r"Use $x = 3.14$. Next.", [r"Use $x = 3.14$.", "Next."]),
        ("citation", r"See \citep[p. 15]{a.b}. Next.", [r"See \citep[p. 15]{a.b}.", "Next."]),
        ("latex-spacing", r"Use e.\,g. movement. Next.", [r"Use e.\,g. movement.", "Next."]),
        ("url", "See https://example.org/a.b. Next.", ["See https://example.org/a.b.", "Next."]),
    ]
    gold = []
    for name, text, expected in cases:
        rule = tokenize_sentences(text)
        candidate = tokenize_candidate(text)
        gold.append(dict(name=name, text=text, expected=expected, rule=rule, candidate=candidate,
                         rule_correct=rule == expected, candidate_correct=candidate == expected))

    rows = []
    for path in sorted(base.glob("*/after-prose-structure-fix.json")):
        baseline = json.loads(path.read_text())
        paper = baseline["summary"]["paper"]
        blocks = []

        def process(raw):
            normalized = normalize_math(clean_latex(raw))
            start = time.perf_counter()
            rule = tokenize_sentences(normalized)
            rule_time = time.perf_counter() - start
            start = time.perf_counter()
            candidate = tokenize_candidate(normalized)
            candidate_time = time.perf_counter() - start
            # Kiểm tra không mất nội dung; không nhầm kiểm tra này với accuracy.
            assert "".join("".join(candidate).split()) == "".join(normalized.split())
            blocks.append(dict(normalized=normalized, rule=rule, candidate=candidate,
                               rule_time=rule_time, candidate_time=candidate_time))
            return candidate

        tree = build_hierarchy(baseline["source"], prose_processor=process)
        # Cùng input cấu trúc: so sánh đúng từng đoạn thay vì số câu toàn bài.
        assert [b["normalized"] for b in blocks] == [b["normalized"] for b in baseline["blocks"]]
        old_kinds = Counter(n["kind"] for n in baseline["tree"]["nodes"])
        kinds = Counter(n["kind"] for n in tree["nodes"])
        assert {k: v for k, v in old_kinds.items() if k != "sentence"} == {
            k: v for k, v in kinds.items() if k != "sentence"
        }
        row = dict(paper=paper, blocks=len(blocks), changed_blocks=sum(b["rule"] != b["candidate"] for b in blocks),
                   rule_sentences=old_kinds["sentence"], candidate_sentences=kinds["sentence"],
                   rule_seconds=sum(b["rule_time"] for b in blocks),
                   candidate_seconds=sum(b["candidate_time"] for b in blocks))
        (output / f"{paper}.json").write_text(json.dumps(dict(summary=row, blocks=blocks, tree=tree), ensure_ascii=False, indent=2))
        rows.append(row)
        print(json.dumps(row))
    result = dict(gold=gold, papers=rows)
    (output / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print("Targeted cases:", "rule", sum(c["rule_correct"] for c in gold),
          "candidate", sum(c["candidate_correct"] for c in gold), "of", len(gold))


if __name__ == "__main__":
    main()
