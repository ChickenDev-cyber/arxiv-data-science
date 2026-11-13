"""Conservative cleanup of LaTeX formatting without interpreting TeX."""

import re

from .utils import _COMMAND, _MATH_ENV, _command_end, _group_end, _math_end

_DROP =     {"centering", "raggedright", "clearpage", "newpage",
            "midrule", "toprule", "bottomrule"}
_UNWRAP =   {"textbf", "emph", "textit", "textrm", "textnormal", "underline"}
def normalize_math(text: str) -> str:
    r"""Normalize inline math to $...$ and display math to equation.
    """
    output: list[str] = []
    i = 0
    while i < len(text):
        delimiters = next(
                ((opening, closing) for opening, closing in
                (("$$", "$$"), ("$", "$"), (r"\(", r"\)"), (r"\[", r"\]"))
                    if text.startswith(opening, i)),
                None,
        )

        inline = False
        if delimiters:
            opening, closing = delimiters
            inline = opening in {"$", r"\("}
        else:
            # Tìm \begin{<name>}
            environment = re.match(r"\\begin\{([A-Za-z]+\*?)\}", text[i:]) if text[i] == "\\" else None
            if environment and environment[1].rstrip("*") in _MATH_ENV:
                opening = environment[0]
                closing = rf"\end{{{environment[1]}}}"
                inline = environment[1] == "math"
            else:
                # Consume escapes together so \$ and \\[ are not math openers.
                end = min(i + 2, len(text)) if text[i] == "\\" else i + 1
                output.append(text[i:end])
                i = end
                continue

        end = _math_end(text, i, opening, closing)
        close_start = end - len(closing)

        escape_start = close_start
        while escape_start > 0 and text[escape_start - 1] == "\\":
            escape_start -= 1

        if (end < i + len(opening) + len(closing)
                or not text.startswith(closing, close_start)
                or (close_start - escape_start) % 2):
            output.append(text[i:])
            break

        inner = text[i + len(opening):end - len(closing)].strip()
        if inline:
            output.append(f"${inner}$")
        else:
            output.append(f"\\begin{{equation}}\n{inner}\n\\end{{equation}}")
        i = end

    return "".join(output)


def clean_latex(text: str) -> str:
    r"""Remove known layout commands and unwrap prose styles.
    """
    output: list[str] = []

    i = 0
    while i < len(text):
        char = text[i]

        # Nếu là một khoảng trắng
        if char.isspace():
            if output and not output[-1].endswith(" "):
                output.append(" ")
            i += 1
            continue

        # Tìm kiếm các ký tự delimiter xung quanh vị trí i
        delimiters = next(((a, b) for a, b in
                            (("$$", "$$"), ("$", "$"), (r"\(", r"\)"), (r"\[", r"\]"))
                            if text.startswith(a, i)), None)

        if delimiters:
            # Nếu thì thấy một delimeter của math tồn tại thì giữ nguyên nó.
            end = _math_end(text, i, *delimiters) # *delimeter = truyền 1 pair theo lần lượt biến opening và closing.
            output.append(text[i:end])

            i = end
            continue

        # Dùng regex để tìm ra command
        # Ví dụ \textbf{textbf} match ra \textbf
        match = _COMMAND.match(text, i)
        if match is None:
            # Nếu không tìm ra command thì giữ nguyên ký tự.

            # Ta lấy min(i + 2, len(text)) là vì command bắt đầu bằng \ thì có thể có 2 ký tự.
            # Ví dụ \begin match ra \begin thì cần i + 6 ký tự.
            end = min(i + 2, len(text)) if char == "\\" else i + 1
            output.append(text[i:end])
            i = end
            continue

        name = match[1]
        end = match.end()

        # Lọc ra các khoảng trắng
        arg_start = end
        while arg_start < len(text) and text[arg_start].isspace():
            arg_start += 1

        if name in _DROP:
            if output and not output[-1].endswith(" "):
                output.append(" ")
            i = end
            continue

        if name == "caption":
            # Chỉ caption có đối số chứa prose được cleanup đệ quy.
            # Giữ nguyên command, dấu * và ngoặc để figure vẫn là một node.
            cursor = arg_start
            groups = []
            if cursor < len(text) and text[cursor] == "[":
                end_option = _group_end(text, cursor)
                if end_option is not None:
                    groups.append((cursor, end_option))
                    cursor = end_option
                    while cursor < len(text) and text[cursor].isspace():
                        cursor += 1
            if cursor < len(text) and text[cursor] == "{":
                end_caption = _group_end(text, cursor)
                if end_caption is not None:
                    groups.append((cursor, end_caption))
                    previous = i
                    for start_group, end_group in groups:
                        output.append(text[previous:start_group + 1])
                        output.append(clean_latex(text[start_group + 1:end_group - 1]))
                        output.append(text[end_group - 1:end_group])
                        previous = end_group
                    i = end_caption
                    continue

        if name in _UNWRAP or name in {"vspace", "hspace"}:
            if arg_start < len(text) and text[arg_start] == "{":
                group_end = _group_end(text, arg_start)
                if group_end is None:
                    output.append(text[i:])
                    break

                if name in _UNWRAP:
                    output.append(clean_latex(text[arg_start + 1:group_end - 1]))
                elif output and not output[-1].endswith(" "):
                    output.append(" ")

                i = group_end
                continue

        if name == "begin" and arg_start < len(text) and text[arg_start] == "{":
            group_end = _group_end(text, arg_start)
            if group_end is not None:
                env = text[arg_start + 1:group_end - 1]
                if env.rstrip("*") in _MATH_ENV:
                    closing = rf"\end{{{env}}}"
                    end = _math_end(text, i, text[i:group_end], closing)
                    output.append(text[i:end])
                    i = end
                    continue

                if env in {"figure", "figure*", "table", "table*"}:
                    option = group_end
                    while option < len(text) and text[option].isspace():
                        option += 1

                    placement = re.match(r"\[[htbpH!\s]+\]", text[option:])
                    output.append(text[i:group_end])
                    i = option + placement.end() if placement else group_end
                    continue

        # Giữ nguyên command chưa hỗ trợ cùng toàn bộ đối số của nó.
        end = _command_end(text, end)
        output.append(text[i:end])
        i = end

    return "".join(output).strip()
