r"""Inline \input/\include recursively, following the compilation order."""

import posixpath
from dataclasses import dataclass, field

from .latex_scan import (
    document_body_span, endinput_cut, expand_simple_macros, find_includes, simple_macros)
from .source_tree import SourceTree

DEFAULT_MAX_DEPTH = 64


@dataclass
class Expansion:
    merged_tex: str
    compilation_path: list[str]
    missing: list[str] = field(default_factory=list)   # "file.tex: \input{x}"
    cycles: list[str] = field(default_factory=list)    # "a.tex -> b.tex -> a.tex"


class IncludeExpander:
    r"""Build merged_tex from a root file.

    - Comments stay in the text but a command inside a comment is not loaded.
    - Targets resolve from the root's folder first, then the including file's.
    - A file already on the include stack (a cycle) is not loaded again; the
      command is dropped. Including the same file twice without a cycle is
      inlined twice, like LaTeX does, but listed once in compilation_path.
    - A target that cannot be found keeps its original command text. Targets
      built from macro parameters (#1) or left empty are not reported.
    - Paths may use simple text macros defined earlier (\newcommand{\dir}{sec}).
    - Lookup order: root file's folder, including file's folder, source_dir.
    - \subfile{x} (subfiles package) inlines only the body of x, between its
      \begin{document} and \end{document}; x's own preamble is skipped.
    """

    def __init__(self, tree: SourceTree, *, max_depth: int = DEFAULT_MAX_DEPTH):
        self.tree = tree
        self.max_depth = max_depth

    def expand(self, root_file: str) -> Expansion:
        self._root_dir = posixpath.dirname(root_file)
        self._result = Expansion(merged_tex="", compilation_path=[])
        self._macros: dict[str, str] = {}
        parts: list[str] = []
        self._inline(root_file, [root_file], parts)
        self._result.merged_tex = "".join(parts)
        return self._result

    def _inline(self, path: str, stack: list[str], parts: list[str], *,
                body_only: bool = False) -> None:
        if path not in self._result.compilation_path:
            self._result.compilation_path.append(path)
        text, masked = self.tree.read(path)
        cut = endinput_cut(masked)
        if cut is not None:
            text, masked = text[:cut], masked[:cut]
        if body_only and (span := document_body_span(masked)) is not None:
            text, masked = text[span[0]:span[1]], masked[span[0]:span[1]]

        self._macros.update(simple_macros(masked))
        position = 0
        bases = [self._root_dir, posixpath.dirname(path), ""]
        for command in find_includes(masked):
            parts.append(text[position:command.start])
            position = command.end
            original = text[command.start:command.end]
            wanted = expand_simple_macros(command.target, self._macros)
            target = None if "\\" in wanted else self.tree.resolve(wanted, bases)
            if target is None:
                if wanted.strip() and "#" not in wanted:
                    self._result.missing.append(f"{path}: {original}")
                parts.append(original)
            elif target in stack or len(stack) >= self.max_depth:
                self._result.cycles.append(" -> ".join([*stack, target]))
            else:
                self._inline(target, [*stack, target], parts,
                             body_only=command.command == "subfile")
                if parts and parts[-1] and not parts[-1].endswith("\n"):
                    # A trailing "% ..." line must not swallow the text after
                    # the command in the including file.
                    parts.append("\n")
        parts.append(text[position:])
