r"""Pick the root .tex file: the one LaTeX would compile."""

import posixpath
from dataclasses import dataclass

from .expander import IncludeExpander
from .latex_scan import document_class, find_includes, has_begin_document
from .source_tree import SourceTree

PREFERRED_NAMES = frozenset({"main.tex", "ms.tex", "paper.tex", "article.tex", "manuscript.tex"})
EDITOR_TEMP_NAMES = frozenset({"_region_.tex"})   # AUCTeX preview file, never the paper


@dataclass(frozen=True)
class RootCandidate:
    path: str
    document_class: str
    has_begin_document: bool
    included_by_other: bool
    merged_chars: int   # length of the text this file would compile to

    def sort_key(self):
        # Smaller sorts first: an editor temp file is last; a real document beats
        # a standalone figure or a file that another .tex already \input-s.
        # arXiv compiles the main file from the top folder, so a top-level file
        # beats old copies and template docs in subfolders (OldFiles/, doc/).
        # Then common names, then the file that compiles to the most text.
        return (
            posixpath.basename(self.path).lower() in EDITOR_TEMP_NAMES,
            not self.has_begin_document,
            self.document_class in ("standalone", "subfiles"),
            self.included_by_other,
            "/" in self.path,
            posixpath.basename(self.path).lower() not in PREFERRED_NAMES,
            -self.merged_chars,
            self.path.count("/"),
            self.path,
        )


class RootFinder:
    r"""Find files with an uncommented \documentclass and rank them."""

    def candidates(self, tree: SourceTree) -> list[RootCandidate]:
        included = self._included_files(tree)
        found = []
        for path in tree.tex_files:
            _, masked = tree.read(path)
            name = document_class(masked)
            if name is None:
                continue
            found.append(RootCandidate(
                path=path,
                document_class=name,
                has_begin_document=has_begin_document(masked),
                included_by_other=path in included,
                merged_chars=len(IncludeExpander(tree).expand(path).merged_tex),
            ))
        return sorted(found, key=RootCandidate.sort_key)

    def find(self, tree: SourceTree) -> str | None:
        ranked = self.candidates(tree)
        return ranked[0].path if ranked else None

    @staticmethod
    def _included_files(tree: SourceTree) -> set[str]:
        included = set()
        for path in tree.tex_files:
            _, masked = tree.read(path)
            folder = posixpath.dirname(path)
            for command in find_includes(masked):
                target = tree.resolve(command.target, [folder, ""])
                if target is not None and target != path:
                    included.add(target)
        return included
