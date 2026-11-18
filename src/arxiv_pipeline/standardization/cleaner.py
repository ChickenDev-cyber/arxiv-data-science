"""Conservative cleanup of LaTeX formatting without interpreting TeX."""

import re

from .utils import _COMMAND, _MATH_ENV, _command_end, _group_end, _math_end

_DROP =     {"centering", "raggedright", "clearpage", "newpage",
            "midrule", "toprule", "bottomrule", "flushbottom", "raggedbottom",
            "noindent", "smallskip", "medskip", "bigskip", "selectfont",
            "tiny", "scriptsize", "footnotesize", "small", "normalsize",
            "large", "Large", "LARGE", "huge", "Huge",
            "IEEEoverridecommandlockouts", "IEEEpeerreviewmaketitle"}
# Consume exactly the known arguments; a following group may contain real prose.
_LAYOUT_ARITY = {"setlength": 2, "addtolength": 2, "setcounter": 2,
                 "addtocounter": 2, "fontsize": 2,
                 "pagestyle": 1, "thispagestyle": 1,
                 "bstctlcite": 1, "SetKwComment": 3, "SetKwProg": 4,
                 "SetKwInOut": 2}
_UNWRAP =   {"textbf", "emph", "textit", "textrm", "textnormal", "underline"}


def _layout_body(text: str, name: str, start: int) -> tuple[str, int] | None:
    """Expose the content of complete, known layout wrappers only."""
    if name == "twocolumn":
        if start >= len(text) or text[start] != "[":
            return None
        end = _group_end(text, start)
        if end is None:
            return None
        body = text[start + 1:end - 1].strip()
        # The common \twocolumn[{...}] idiom adds one scope-only group.
        if body.startswith("{") and _group_end(body, 0) == len(body):
            body = body[1:-1]
        return body, end
    if name not in {"resizebox", "scalebox"}:
        return None
    pos = start
    for _ in range(2 if name == "resizebox" else 1):
        if pos >= len(text) or text[pos] != "{":
            return None
        end = _group_end(text, pos)
        if end is None:
            return None
        pos = end
        while pos < len(text) and text[pos].isspace():
            pos += 1
    if name == "scalebox" and pos < len(text) and text[pos] == "[":
        end = _group_end(text, pos)
        if end is None:
            return None
        pos = end
        while pos < len(text) and text[pos].isspace():
            pos += 1
    if pos < len(text) and text[pos] == "{":
        end = _group_end(text, pos)
        if end is not None:
            return text[pos + 1:end - 1], end
    return None


def _definition_end(text: str, name: str, start: int) -> int | None:
    """Skip a complete macro definition, never interpret its replacement as prose.

    Incomplete/unknown forms are retained. Macro uses are still preserved; this
    is not TeX expansion and does not guess the meaning of an unknown command.
    """
    if name not in {"newcommand", "renewcommand", "providecommand", "DeclareRobustCommand",
                    "def", "gdef", "edef", "xdef"}:
        return None
    pos = start
    if pos < len(text) and text[pos] == "{":
        end = _group_end(text, pos)
        if end is None:
            return None
        pos = end
    else:
        command = _COMMAND.match(text, pos)
        if command is None:
            return None
        pos = command.end()
    if name in {"def", "gdef", "edef", "xdef"}:
        # Parameters (#1, delimiters, ...) precede the replacement group.
        while pos < len(text) and text[pos] != "{":
            if text[pos] == "\\":
                return None
            pos += 1
    else:
        for _ in range(2):  # optional argument count and default value
            while pos < len(text) and text[pos].isspace():
                pos += 1
            if pos < len(text) and text[pos] == "[":
                end = _group_end(text, pos)
                if end is None:
                    return None
                pos = end
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return _group_end(text, pos) if pos < len(text) and text[pos] == "{" else None


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

        # A TeX dimension assignment is layout, not a prose sentence. Only
        # recognize a complete literal dimension; retain unsupported forms.
        if name == "parindent":
            dimension = re.match(r"=?\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*"
                                 r"(?:pt|pc|in|bp|cm|mm|dd|cc|sp|em|ex)\b", text[arg_start:])
            if dimension:
                if output and not output[-1].endswith(" "):
                    output.append(" ")
                i = arg_start + dimension.end()
                continue

        definition_end = _definition_end(text, name, arg_start)
        layout_end = arg_start
        if name in _LAYOUT_ARITY:
            for index in range(_LAYOUT_ARITY[name]):
                while layout_end < len(text) and text[layout_end].isspace():
                    layout_end += 1
                group_end = (_group_end(text, layout_end)
                             if layout_end < len(text) and text[layout_end] == "{" else None)
                if group_end is None and index == 0 and name in {"setlength", "addtolength"}:
                    register = _COMMAND.match(text, layout_end)
                    if register is not None:
                        group_end = register.end()
                if group_end is None:
                    layout_end = None
                    break
                layout_end = group_end
        else:
            layout_end = None
        if definition_end is not None or layout_end is not None:
            if output and not output[-1].endswith(" "):
                output.append(" ")
            i = definition_end if definition_end is not None else layout_end
            continue

        wrapper = _layout_body(text, name, arg_start)
        if wrapper is not None:
            body, wrapper_end = wrapper
            output.append(clean_latex(body))
            i = wrapper_end
            continue

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
