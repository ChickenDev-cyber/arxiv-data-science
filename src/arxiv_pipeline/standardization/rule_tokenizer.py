"""Tách câu theo dấu chấm, bảo toàn các vùng đặc biệt của LaTeX."""

import re
from pathlib import Path

from .utils import _COMMAND, _MATH_ENV, _command_end, _math_end

# Chỉ hỗ trợ các lệnh spacing đã biết, không nuốt command LaTeX bất kỳ.
_SPACING = r"(?:\s|~|\\[,;:! ])"


def _load_abbreviations() -> re.Pattern[str]:
    """Đọc vocab một lần khi import, chuyển chữ thường thành pattern an toàn."""
    patterns = []
    # Đường dẫn theo module để chạy CLI từ thư mục nào cũng tìm được vocab.
    for line in Path(__file__).with_name("vocab.txt").read_text(encoding="utf-8").splitlines():
        word = line.strip()
        if not word or word.startswith("#"):
            continue
        parts = []
        for i, char in enumerate(word):
            if char.isspace():
                parts.append(_SPACING + "+")
            else:
                # Escape để vocab là dữ liệu, không thể vô tình trở thành regex.
                parts.append(re.escape(char))
                # e.g. và e.\,g. có cùng nghĩa: cho phép spacing sau dấu chấm
                # nội bộ, nhưng không nuốt khoảng trắng sau dấu chấm cuối.
                if char == "." and i + 1 < len(word) and word[i + 1].isalpha():
                    parts.append(_SPACING + "*")
        patterns.append("".join(parts))
    # Ưu tiên mẫu dài để không nhận một phần của viết tắt nhiều thành phần.
    alternatives = "|".join(sorted(patterns, key=len, reverse=True))
    return re.compile(r"\b(?:" + alternatives + ")", re.IGNORECASE) if patterns else re.compile(r"(?!)")


_ABBREVIATION = _load_abbreviations()
_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"“”]+", re.IGNORECASE)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_ENVIRONMENT = re.compile(r"\\begin\{([A-Za-z]+\*?)\}")


def tokenize_sentences(text: str) -> list[str]:
    r"""Tách prose thành câu; giữ dấu chấm và nội dung gốc trong mỗi câu.

    Không tách trong math, đối số command, viết tắt, decimal, URL/email.
    Chỉ bỏ khoảng trắng ở ranh giới câu; không sửa khoảng trắng trong math.
    Block formula/figure thông thường đã được hierarchy tách trước bước này.
    """
    sentences: list[str] = []
    start = i = 0
    while i < len(text):
        # Thử $$ trước $ để không nhận nhầm display math thành inline math.
        delimiters = next(
            ((a, b) for a, b in (("$$", "$$"), ("$", "$"),
                                (r"\(", r"\)"), (r"\[", r"\]"))
                if text.startswith(a, i)), None,
        )
        if delimiters:
            i = _math_end(text, i, *delimiters)
            continue

        if text[i] == "\\":
            environment = _ENVIRONMENT.match(text, i)
            if environment and environment[1].rstrip("*") in _MATH_ENV:
                i = _math_end(text, i, environment[0], rf"\end{{{environment[1]}}}")
                continue
            command = _COMMAND.match(text, i)
            if command:
                # Không dùng regex {[^}]*}: nó dừng sai ở ngoặc đóng bên trong.
                i = _command_end(text, command.end())
            else:
                # Bỏ qua cả cặp escape để \$ không trở thành dấu mở math.
                i = min(i + 2, len(text))
            continue

        # Kiểm tra tại đầu token để tránh nhận URL/viết tắt ở giữa một từ.
        boundary = i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_")
        if boundary:
            url = _URL.match(text, i)
            if url:
                end = url.end()
                # Dấu chấm cuối URL có thể kết thúc câu; không bảo vệ nó.
                # Ngoặc đóng cân bằng trong URL vẫn thuộc URL (vd. Wikipedia).
                while end > i and text[end - 1] in ".,;:!?)]}'’":
                    char = text[end - 1]
                    opening = {")": "(", "]": "[", "}": "{"}.get(char)
                    if opening and text[i:end].count(char) <= text[i:end].count(opening):
                        break
                    end -= 1
                i = end
                continue
            protected = _EMAIL.match(text, i) or _ABBREVIATION.match(text, i)
            if protected:
                i = protected.end()
                continue

        if text[i] == ".":
            # 3.14 hoặc 1.2.3 không tạo ranh giới câu giữa hai chữ số.
            if i > 0 and i + 1 < len(text) and text[i - 1].isdigit() and text[i + 1].isdigit():
                i += 1
                continue
            end = i + 1
            # Giữ dấu ... cùng nhau, và giữ ngoặc/ngoặc kép đóng với câu trước.
            while end < len(text) and text[end] in ".\"'’”)]}":
                end += 1
            if end == len(text) or text[end].isspace():
                sentence = text[start:end].strip()
                if sentence:
                    sentences.append(sentence)
                start = i = end
                continue
        i += 1

    # Không bỏ mất câu cuối dù nó chưa có dấu chấm kết thúc.
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences
