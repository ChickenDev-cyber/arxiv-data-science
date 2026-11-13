"""Tiện ích quét LaTeX dùng chung cho cleaner và tokenizer."""

import re

_COMMAND =  re.compile(r"\\([A-Za-z]+)(\*?)")
_MATH_ENV = {"math", "displaymath", "equation", "align", "gather",
            "multline", "eqnarray", "flalign", "alignat"}


def _group_end(text: str, start: int) -> int | None:
    """Trả vị trí sau nhóm ngoặc cân bằng; None nếu thiếu ngoặc đóng."""
    stack = ["}" if text[start] == "{" else "]"]
    i = start + 1
    while i < len(text):
        char = text[i]
        if char == "\\":
            # Ngoặc đã escape là ký tự nội dung, không làm thay đổi độ sâu.
            i += 2
            continue
        if char == "{":
            stack.append("}")
        elif char == "[" and stack[-1] == "]":
            stack.append("]")
        elif char == stack[-1]:
            stack.pop()
            if not stack:
                return i + 1
        i += 1
    return None


def _math_end(text: str, start: int, opening: str, closing: str) -> int:
    # Bắt đầu sau dấu mở để không nhầm nó với dấu đóng khi cùng là $.
    i = start + len(opening)
    while i < len(text):
        if text.startswith(closing, i):
            return i + len(closing)
        i += 2 if text[i] == "\\" else 1
    return len(text)  # Thiếu dấu đóng: bảo vệ toàn bộ phần còn lại.


def _command_end(text: str, end: int) -> int:
    """Tìm hết đối số của command, bao gồm ngoặc lồng nhau."""
    while end < len(text):
        arg_start = end
        while arg_start < len(text) and text[arg_start].isspace():
            arg_start += 1
        if arg_start == len(text) or text[arg_start] not in "{[":
            break
        group_end = _group_end(text, arg_start)
        # Thiếu ngoặc đóng: giữ phần còn lại thay vì cắt bên trong đối số.
        if group_end is None:
            return len(text)
        end = group_end
    return end
