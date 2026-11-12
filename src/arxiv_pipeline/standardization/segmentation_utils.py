"""Bảo vệ LaTeX và ánh xạ ranh giới dùng chung cho các adapter thử nghiệm."""

from .rule_tokenizer import _ABBREVIATION, _EMAIL, _ENVIRONMENT, _URL
from .utils import _COMMAND, _MATH_ENV, _command_end, _math_end


def _protected_regions(text: str) -> list[tuple[int, int]]:
    regions = []
    i = 0
    while i < len(text):
        start = i
        delimiters = next(((a, b) for a, b in (("$$", "$$"), ("$", "$"),
                          (r"\(", r"\)"), (r"\[", r"\]")) if text.startswith(a, i)), None)
        if delimiters:
            i = _math_end(text, i, *delimiters)
        elif text[i] == "\\":
            env = _ENVIRONMENT.match(text, i)
            command = _COMMAND.match(text, i)
            if env and env[1].rstrip("*") in _MATH_ENV:
                i = _math_end(text, i, env[0], rf"\end{{{env[1]}}}")
            elif command:
                i = _command_end(text, command.end())
            else:
                i = min(i + 2, len(text))
        elif i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_"):
            url = _URL.match(text, i)
            protected = _EMAIL.match(text, i) or _ABBREVIATION.match(text, i)
            if url:
                i = url.end()
                # Chừa dấu chấm kết thúc câu bên ngoài URL, giữ ngoặc cân bằng.
                while i > start and text[i - 1] in ".,;:!?)]}'’":
                    char = text[i - 1]
                    opening = {")": "(", "]": "[", "}": "{"}.get(char)
                    if opening and text[start:i].count(char) <= text[start:i].count(opening):
                        break
                    i -= 1
            elif protected:
                i = protected.end()
        if i == start:
            i += 1
        else:
            regions.append((start, i))
    return regions


def masked_view(text: str):
    regions = _protected_regions(text)
    view = list(text)
    for start, end in regions:
        # Thay vùng kỹ thuật bằng chữ, giữ nguyên độ dài và whitespace.
        # Offset của model do đó dùng trực tiếp để cắt chuỗi gốc; không có
        # placeholder nào được ghép trở lại hoặc lọt vào output.
        view[start:end] = [char if char.isspace() else "x" for char in text[start:end]]
    return "".join(view), regions


def split_at_ends(text: str, ends, regions) -> list[str]:
    sentences = []
    start = region_index = 0
    for end in ends:
        # Cắt từ chuỗi gốc: ngoặc/ngoặc kép đóng thuộc câu trước.
        while end < len(text) and text[end] in "\"'’”)]}":
            end += 1
        # Giữ contract ranh giới hiện tại: không cắt giữa ref và hậu tố .(ii),
        # hoặc trước command dính liền. Bản mask có thể làm model đoán sai ở đây.
        if end < len(text) and not text[end].isspace():
            continue
        while region_index < len(regions) and regions[region_index][1] <= end:
            region_index += 1
        if region_index < len(regions) and regions[region_index][0] < end < regions[region_index][1]:
            continue
        # Model có thể tách !/?; contract hiện tại chỉ nhận dấu chấm.
        punctuation = end - 1
        while punctuation >= start and text[punctuation] in "\"'’”)]}":
            punctuation -= 1
        if end != len(text.rstrip()) and (punctuation < start or text[punctuation] != "."):
            continue
        sentence = text[start:end].strip()
        if sentence:
            sentences.append(sentence)
        start = end
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences
