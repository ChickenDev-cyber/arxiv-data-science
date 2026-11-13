"""So sánh thử nghiệm Punkt với baseline; không đổi tokenizer mặc định.

Chạy với PYTHONPATH=src và NLTK_DATA trỏ tới model punkt_tab đã tải.
"""

import json
import time
from collections import Counter
from pathlib import Path

from arxiv_pipeline.hierarchy import build_hierarchy
from arxiv_pipeline.standardization import clean_latex, normalize_math
from arxiv_pipeline.standardization.rule_tokenizer import tokenize_sentences
from arxiv_pipeline.standardization.punkt_adapter import _model, tokenize_sentences_punkt


def main():
    base = Path("data/module2-khang-review")
    output = base / "punkt-experiment"
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
        punkt = tokenize_sentences_punkt(text)
        gold.append(dict(name=name, text=text, expected=expected, rule=rule, punkt=punkt,
                         rule_correct=rule == expected, punkt_correct=punkt == expected))

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
            punkt = tokenize_sentences_punkt(normalized)
            punkt_time = time.perf_counter() - start
            # Kiểm tra không mất nội dung; không nhầm kiểm tra này với accuracy.
            assert "".join("".join(punkt).split()) == "".join(normalized.split())
            blocks.append(dict(normalized=normalized, rule=rule, punkt=punkt,
                               rule_time=rule_time, punkt_time=punkt_time))
            return punkt

        tree = build_hierarchy(baseline["source"], prose_processor=process)
        # Cùng input cấu trúc: so sánh đúng từng đoạn thay vì số câu toàn bài.
        assert [b["normalized"] for b in blocks] == [b["normalized"] for b in baseline["blocks"]]
        old_kinds = Counter(n["kind"] for n in baseline["tree"]["nodes"])
        kinds = Counter(n["kind"] for n in tree["nodes"])
        assert {k: v for k, v in old_kinds.items() if k != "sentence"} == {
            k: v for k, v in kinds.items() if k != "sentence"
        }
        row = dict(paper=paper, blocks=len(blocks), changed_blocks=sum(b["rule"] != b["punkt"] for b in blocks),
                   rule_sentences=old_kinds["sentence"], punkt_sentences=kinds["sentence"],
                   rule_seconds=sum(b["rule_time"] for b in blocks),
                   punkt_seconds=sum(b["punkt_time"] for b in blocks))
        (output / f"{paper}.json").write_text(json.dumps(dict(summary=row, blocks=blocks, tree=tree), ensure_ascii=False, indent=2))
        rows.append(row)
        print(json.dumps(row))
    result = dict(gold=gold, papers=rows)
    (output / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print("Targeted cases:", "rule", sum(c["rule_correct"] for c in gold),
          "punkt", sum(c["punkt_correct"] for c in gold), "of", len(gold))


if __name__ == "__main__":
    main()
