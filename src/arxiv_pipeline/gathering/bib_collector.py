r"""Collect the .bib files that the compiled text really asks for."""

import posixpath

from .latex_scan import find_bib_names, mask_comments
from .source_tree import SourceTree


class BibCollector:
    r"""List .bib files named by \bibliography{...} / \addbibresource{...}.

    Only commands that are not in a comment count, and only files that exist
    in source_dir. Nothing is globbed: a template's leftover sample.bib that
    no command mentions is not returned. BibTeX reads names relative to the
    root file's folder; folders of the other files in the compilation path,
    then source_dir itself, are fallbacks. A name without ".bib" gets it added.
    """

    def __init__(self, tree: SourceTree):
        self.tree = tree

    def collect(self, merged_tex: str, root_file: str,
                compilation_path: list[str]) -> tuple[list[str], list[str]]:
        """Return (bib_files, missing_names), both in order of appearance."""
        bases = [posixpath.dirname(root_file)]
        bases += [posixpath.dirname(path) for path in compilation_path] + [""]
        found: list[str] = []
        missing: list[str] = []
        for name in find_bib_names(mask_comments(merged_tex)):
            path = self.tree.resolve_bib(name, bases)
            if path is None:
                if name not in missing:
                    missing.append(name)
            elif path not in found:
                found.append(path)
        return found, missing
