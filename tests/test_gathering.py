import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from arxiv_pipeline.gathering import GATHERED_SOURCE_KEYS, SourceGatherer, gather_source
from arxiv_pipeline.gathering.cli import main as cli_main
from arxiv_pipeline.gathering.cli import paper_id_from_folder
from arxiv_pipeline.gathering.latex_scan import (
    comment_start, find_bib_names, find_includes, mask_comments)

ID = "2409.03594"


class GatherTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "v1"
        self.root.mkdir()

    def write(self, name, text, encoding="utf-8"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode(encoding))
        return path

    def gather(self):
        return gather_source(self.root, ID, 1)

    def inspect(self):
        return SourceGatherer().inspect(self.root, ID, 1)


class LatexScanTest(unittest.TestCase):
    def test_comment_start_handles_escaped_percent(self):
        self.assertEqual(comment_start(r"50\% done"), -1)
        self.assertEqual(comment_start(r"line\\% comment"), 6)
        self.assertEqual(comment_start("a % b"), 2)

    def test_mask_keeps_length_and_hides_comments(self):
        text = "a \\input{x} % \\input{y}\n% \\include{z}\nb"
        masked = mask_comments(text)
        self.assertEqual(len(masked), len(text))
        self.assertEqual([c.target for c in find_includes(masked)], ["x"])

    def test_similar_command_names_are_not_includes(self):
        text = r"\includegraphics{fig} \inputencoding{utf8} \includeonly{a} \input{real}"
        self.assertEqual([c.target for c in find_includes(text)], ["real"])

    def test_bare_input_without_braces(self):
        commands = find_includes("\\input intro\n")
        self.assertEqual([(c.command, c.target) for c in commands], [("input", "intro")])


class StatusTest(GatherTestCase):
    def test_single_file_ok(self):
        body = "\\documentclass{article}\n\\begin{document}\nHi.\n\\end{document}\n"
        self.write("main.tex", body)
        source = self.gather()
        self.assertEqual(source["status"], "ok")
        self.assertEqual(source["root_file"], "main.tex")
        self.assertEqual(source["compilation_path"], ["main.tex"])
        self.assertEqual(source["merged_tex"], body)

    def test_empty_directory_is_empty_source(self):
        source = self.gather()
        self.assertEqual(source["status"], "empty_source")
        self.assertEqual((source["root_file"], source["compilation_path"], source["merged_tex"]),
                         (None, [], ""))

    def test_pdf_only_version_with_manifest_only_is_empty_source(self):
        self.write("source_manifest.json", "{}")
        self.assertEqual(self.gather()["status"], "empty_source")

    def test_missing_directory_is_empty_source(self):
        source = gather_source(self.root / "nope", ID, 2)
        self.assertEqual((source["status"], source["version"]), ("empty_source", 2))

    def test_commented_documentclass_is_no_root_file(self):
        self.write("main.tex", "% \\documentclass{article}\nText\n")
        source = self.gather()
        self.assertEqual(source["status"], "no_root_file")
        self.assertEqual((source["root_file"], source["compilation_path"], source["merged_tex"]),
                         (None, [], ""))

    def test_old_documentstyle_counts_as_root(self):
        self.write("paper.tex", "\\documentstyle[12pt]{article}\n\\begin{document}x\\end{document}")
        self.assertEqual(self.gather()["root_file"], "paper.tex")


class IncludeTest(GatherTestCase):
    def test_input_without_extension_and_relative_paths(self):
        self.write("main.tex", "\\documentclass{article}\n\\begin{document}\n"
                               "\\input{sections/intro}\n\\include{./sections/method.tex}\n"
                               "\\end{document}\n")
        self.write("sections/intro.tex", "INTRO\n")
        self.write("sections/method.tex", "METHOD\n")
        source = self.gather()
        self.assertEqual(source["compilation_path"],
                         ["main.tex", "sections/intro.tex", "sections/method.tex"])
        self.assertEqual(source["merged_tex"],
                         "\\documentclass{article}\n\\begin{document}\nINTRO\n\n"
                         "METHOD\n\n\\end{document}\n")

    def test_nested_input_resolves_from_root_folder_first(self):
        # LaTeX resolves sections/a.tex's \input{sections/b} from main.tex's folder.
        self.write("main.tex", "\\documentclass{article}\\input{sections/a}")
        self.write("sections/a.tex", "A \\input{sections/b}")
        self.write("sections/b.tex", "B")
        source = self.gather()
        self.assertEqual(source["compilation_path"], ["main.tex", "sections/a.tex", "sections/b.tex"])
        self.assertIn("A B", source["merged_tex"])

    def test_nested_input_falls_back_to_including_file_folder(self):
        self.write("main.tex", "\\documentclass{article}\\input{sec/a}")
        self.write("sec/a.tex", "A \\input{b}")
        self.write("sec/b.tex", "B")
        self.assertEqual(self.gather()["compilation_path"], ["main.tex", "sec/a.tex", "sec/b.tex"])

    def test_root_in_subfolder_resolves_from_its_own_folder(self):
        self.write("paper/main.tex", "\\documentclass{article}\\input{intro}")
        self.write("paper/intro.tex", "INTRO")
        source = self.gather()
        self.assertEqual(source["root_file"], "paper/main.tex")
        self.assertEqual(source["compilation_path"], ["paper/main.tex", "paper/intro.tex"])

    def test_commented_input_is_plain_text(self):
        self.write("main.tex", "\\documentclass{article}\n% \\input{old}\n50\\% \\input{new}\n")
        self.write("old.tex", "OLD")
        self.write("new.tex", "NEW")
        source = self.gather()
        self.assertEqual(source["compilation_path"], ["main.tex", "new.tex"])
        self.assertIn("% \\input{old}", source["merged_tex"])
        self.assertNotIn("OLD", source["merged_tex"])

    def test_trailing_comment_after_live_input_loads_only_first_target(self):
        self.write("main.tex", "\\documentclass{article}\n\\input{sec1} % comment \\input{sec2}\nafter\n")
        self.write("sec1.tex", "S1")
        self.write("sec2.tex", "S2")
        source = self.gather()
        self.assertEqual(source["compilation_path"], ["main.tex", "sec1.tex"])
        self.assertNotIn("S2", source["merged_tex"])

    def test_files_outside_the_compilation_path_are_left_out(self):
        self.write("main.tex", "\\documentclass{article}\\input{used}")
        self.write("used.tex", "USED")
        self.write("unused.tex", "UNUSED")
        self.write("old/main_v0.tex", "OLD DRAFT")
        source = self.gather()
        self.assertEqual(source["compilation_path"], ["main.tex", "used.tex"])
        self.assertNotIn("UNUSED", source["merged_tex"])
        self.assertNotIn("OLD DRAFT", source["merged_tex"])

    def test_circular_input_terminates(self):
        self.write("main.tex", "\\documentclass{article}\\input{a}END")
        self.write("a.tex", "A\\input{b}")
        self.write("b.tex", "B\\input{a}\\input{main}")
        report = self.inspect()
        self.assertEqual(report.source["compilation_path"], ["main.tex", "a.tex", "b.tex"])
        self.assertEqual(report.source["merged_tex"].count("A"), 1)
        self.assertTrue(report.source["merged_tex"].rstrip().endswith("END"))
        self.assertEqual(len(report.cycles), 2)

    def test_self_input_terminates(self):
        self.write("main.tex", "\\documentclass{article}\\input{main}X")
        report = self.inspect()
        self.assertEqual(report.source["compilation_path"], ["main.tex"])
        self.assertEqual(report.cycles, ["main.tex -> main.tex"])

    def test_same_file_twice_is_inlined_twice_and_listed_once(self):
        self.write("main.tex", "\\documentclass{article}\\input{rule}\\input{rule}")
        self.write("rule.tex", "R\n")
        source = self.gather()
        self.assertEqual(source["compilation_path"], ["main.tex", "rule.tex"])
        self.assertEqual(source["merged_tex"].count("R\n"), 2)

    def test_simple_macro_in_path_is_expanded(self):
        self.write("main.tex", "\\documentclass{article}\\newcommand{\\texpath}{tex}"
                               "\\def\\figdir{figs}\\input{\\texpath/intro}\\input{\\figdir/plot}")
        self.write("tex/intro.tex", "INTRO")
        self.write("figs/plot.tex", "PLOT")
        report = self.inspect()
        self.assertEqual(report.source["compilation_path"], ["main.tex", "tex/intro.tex", "figs/plot.tex"])
        self.assertEqual(report.missing_includes, [])

    def test_unknown_macro_in_path_is_reported_missing(self):
        self.write("main.tex", "\\documentclass{article}\\input{\\nowhere/intro}")
        self.write("intro.tex", "INTRO")
        report = self.inspect()
        self.assertEqual(report.source["compilation_path"], ["main.tex"])
        self.assertEqual(len(report.missing_includes), 1)

    def test_quotes_inside_path_are_removed(self):
        self.write("main.tex", '\\documentclass{article}\\input{./Proofs/"Fixed Points"/prf}')
        self.write("Proofs/Fixed Points/prf.tex", "PROOF")
        self.assertIn("PROOF", self.gather()["merged_tex"])

    def test_root_in_subfolder_can_input_from_source_dir(self):
        self.write("latex/acl.tex", "\\documentclass{article}\\input{sections/intro}")
        self.write("sections/intro.tex", "INTRO")
        self.assertEqual(self.gather()["compilation_path"], ["latex/acl.tex", "sections/intro.tex"])

    def test_macro_parameter_and_empty_targets_are_not_reported(self):
        self.write("main.tex", "\\documentclass{article}\\newcommand{\\a}[1]{\\input{#1-x.sum}}\\input{}")
        self.assertEqual(self.inspect().missing_includes, [])

    def test_subfile_inlines_only_the_body(self):
        self.write("tqe_main.tex", "\\documentclass{article}\\usepackage{subfiles}\n"
                                   "\\begin{document}\n\\subfile{./sections_tqe/00intro}\n"
                                   "\\subfile{sections_tqe/01method}\n\\end{document}\n")
        self.write("sections_tqe/00intro.tex", "\\documentclass[../tqe_main.tex]{subfiles}\n"
                                               "\\usepackage{SUBPREAMBLE}\n\\begin{document}\nINTRO\n\\end{document}\n")
        self.write("sections_tqe/01method.tex", "\\documentclass[../tqe_main.tex]{subfiles}\n"
                                                "\\begin{document}\nMETHOD\n\\end{document}\n")
        report = self.inspect()
        source = report.source
        self.assertEqual(source["root_file"], "tqe_main.tex")
        self.assertEqual(source["compilation_path"],
                         ["tqe_main.tex", "sections_tqe/00intro.tex", "sections_tqe/01method.tex"])
        self.assertIn("INTRO", source["merged_tex"])
        self.assertIn("METHOD", source["merged_tex"])
        self.assertNotIn("SUBPREAMBLE", source["merged_tex"])
        self.assertEqual(source["merged_tex"].count("\\begin{document}"), 1)
        self.assertEqual(source["merged_tex"].count("\\end{document}"), 1)

    def test_subfiles_class_file_is_not_chosen_as_root(self):
        self.write("main.tex", "\\documentclass{article}\\begin{document}\\subfile{s}\\end{document}")
        self.write("other/s2.tex", "\\documentclass[../main]{subfiles}\\begin{document}" + "x " * 3000
                                   + "\\end{document}")
        self.write("s.tex", "\\documentclass[main]{subfiles}\\begin{document}S\\end{document}")
        self.assertEqual(self.gather()["root_file"], "main.tex")

    def test_missing_target_keeps_command_and_is_reported(self):
        self.write("main.tex", "\\documentclass{article}\\input{ghost}")
        report = self.inspect()
        self.assertIn("\\input{ghost}", report.source["merged_tex"])
        self.assertEqual(report.missing_includes, ["main.tex: \\input{ghost}"])
        self.assertEqual(report.source["status"], "ok")

    def test_paths_outside_source_dir_are_not_loaded(self):
        (self.root.parent / "secret.tex").write_text("SECRET")
        self.write("main.tex", "\\documentclass{article}\\input{../secret}")
        self.assertNotIn("SECRET", self.gather()["merged_tex"])

    def test_macro_parameter_is_not_a_file(self):
        self.write("main.tex", "\\documentclass{article}\\newcommand{\\sec}[1]{\\input{#1}}")
        report = self.inspect()
        self.assertEqual(report.source["compilation_path"], ["main.tex"])

    def test_case_insensitive_fallback(self):
        self.write("main.tex", "\\documentclass{article}\\input{Intro}")
        self.write("intro.tex", "INTRO")
        self.assertEqual(self.gather()["compilation_path"], ["main.tex", "intro.tex"])

    def test_endinput_stops_reading_the_file(self):
        self.write("main.tex", "\\documentclass{article}\\input{part}AFTER")
        self.write("part.tex", "KEEP \\endinput SAME LINE\nDROPPED\n")
        merged = self.gather()["merged_tex"]
        self.assertIn("KEEP \\endinput SAME LINE\nAFTER", merged)
        self.assertNotIn("DROPPED", merged)

    def test_trailing_comment_in_child_does_not_swallow_parent_text(self):
        self.write("main.tex", "\\documentclass{article}\\input{part} AFTER")
        self.write("part.tex", "PART % note")
        self.assertIn("PART % note\n AFTER", self.gather()["merged_tex"])

    def test_bare_input_is_followed(self):
        self.write("main.tex", "\\documentclass{article}\n\\input intro\n")
        self.write("intro.tex", "INTRO\n")
        self.assertEqual(self.gather()["compilation_path"], ["main.tex", "intro.tex"])

    def test_crlf_and_latin1_are_decoded(self):
        self.write("main.tex", "\\documentclass{article}\r\n\\input{a}\r\n")
        self.write("a.tex", "caf\u00e9\r\n", encoding="latin-1")
        merged = self.gather()["merged_tex"]
        self.assertNotIn("\r", merged)
        self.assertIn("caf\u00e9", merged)


class RootChoiceTest(GatherTestCase):
    def test_prefers_file_with_begin_document(self):
        self.write("aaa.tex", "\\documentclass{article}\n\\newcommand{\\x}{}")
        self.write("zzz.tex", "\\documentclass{article}\n\\begin{document}x\\end{document}")
        self.assertEqual(self.gather()["root_file"], "zzz.tex")

    def test_standalone_figure_loses_to_real_document(self):
        self.write("fig.tex", "\\documentclass{standalone}\\begin{document}tikz\\end{document}"
                              + "%" * 5000)
        self.write("paper.tex", "\\documentclass{article}\\begin{document}x\\end{document}")
        self.assertEqual(self.gather()["root_file"], "paper.tex")

    def test_file_included_by_another_is_not_root(self):
        self.write("main.tex", "\\documentclass{article}\\begin{document}\\input{supp}\\end{document}")
        self.write("supp.tex", "\\documentclass{article}\\begin{document}"
                               + "long " * 2000 + "\\end{document}")
        report = self.inspect()
        self.assertEqual(report.source["root_file"], "main.tex")
        self.assertEqual([c.path for c in report.root_candidates], ["main.tex", "supp.tex"])

    def test_editor_temp_region_file_is_never_the_root(self):
        self.write("_region_.tex", "\\documentclass{article}\\input{pre}\\begin{document}x\\end{document}")
        self.write("pre.tex", "PRE")
        self.write("manu.tex", "\\documentclass{article}\\begin{document}" + "text " * 500 + "\\end{document}")
        self.assertEqual(self.gather()["root_file"], "manu.tex")

    def test_bigger_compiled_document_wins_over_bigger_raw_file(self):
        self.write("a_paper.tex", "\\documentclass{article}\\begin{document}\\input{body}\\end{document}")
        self.write("body.tex", "word " * 3000)
        self.write("b_notes.tex", "\\documentclass{article}\\begin{document}" + "x" * 500 + "\\end{document}")
        self.assertEqual(self.gather()["root_file"], "a_paper.tex")

    def test_top_level_file_beats_old_copy_or_template_doc_in_subfolder(self):
        body = "\\documentclass{article}\\begin{document}%s\\end{document}"
        self.write("OldFiles/main.tex", body % ("old " * 3000))
        self.write("doc/elsdoc.tex", body % ("manual " * 3000))
        self.write("paper_v2.tex", body % "new")
        self.assertEqual(self.gather()["root_file"], "paper_v2.tex")

    def test_preferred_name_breaks_ties(self):
        body = "\\documentclass{article}\\begin{document}x\\end{document}"
        self.write("answer_to_reviewers.tex", body + " " * 50)
        self.write("main.tex", body)
        self.assertEqual(self.gather()["root_file"], "main.tex")


class BibFilesTest(GatherTestCase):
    HEAD = "\\documentclass{article}\n"

    def bibs(self, text=None):
        if text is not None:
            self.write("main.tex", text)
        return self.gather()["bib_files"]

    def test_find_bib_names_handles_lists_options_and_remote(self):
        text = (r"\bibliography{a, latex/b ,c.bib} \addbibresource[location=local]{d.bib}"
                r" \addbibresource{https://x.org/e.bib} \bibliographystyle{plain}")
        self.assertEqual(find_bib_names(text), ["a", "latex/b", "c.bib", "d.bib"])

    def test_extension_is_optional_and_list_is_split(self):
        self.write("refs.bib", "")
        self.write("latex/custom.bib", "")
        self.assertEqual(self.bibs(self.HEAD + "\\bibliography{refs, latex/custom}"),
                         ["refs.bib", "latex/custom.bib"])

    def test_addbibresource_with_option(self):
        self.write("lib.bib", "")
        self.assertEqual(self.bibs(self.HEAD + "\\addbibresource[datatype=bibtex]{lib.bib}"),
                         ["lib.bib"])

    def test_commented_bibliography_is_ignored(self):
        self.write("refs.bib", "")
        self.assertEqual(self.bibs(self.HEAD + "% \\bibliography{refs}\n"), [])

    def test_only_cited_bib_files_not_every_bib_on_disk(self):
        self.write("refs.bib", "")
        self.write("sample-bibliography.bib", "")
        self.write("IEEEexample.bib", "")
        self.assertEqual(self.bibs(self.HEAD + "\\bibliography{refs}"), ["refs.bib"])

    def test_bibliography_inside_included_file_is_found(self):
        self.write("main.tex", self.HEAD + "\\input{tail}")
        self.write("tail.tex", "\\bibliography{refs}")
        self.write("refs.bib", "")
        self.assertEqual(self.gather()["bib_files"], ["refs.bib"])

    def test_bibliography_in_file_that_is_not_loaded_is_ignored(self):
        self.write("main.tex", self.HEAD + "text")
        self.write("unused.tex", "\\bibliography{refs}")
        self.write("refs.bib", "")
        self.assertEqual(self.gather()["bib_files"], [])

    def test_missing_bib_is_skipped_and_reported(self):
        self.write("main.tex", self.HEAD + "\\bibliography{ghost}")
        report = self.inspect()
        self.assertEqual((report.source["bib_files"], report.missing_bib), ([], ["ghost"]))

    def test_duplicates_removed_order_kept(self):
        self.write("a.bib", "")
        self.write("b.bib", "")
        self.assertEqual(self.bibs(self.HEAD + "\\bibliography{b,a}\\bibliography{a}"),
                         ["b.bib", "a.bib"])

    def test_root_in_subfolder_resolves_bib_from_its_folder(self):
        self.write("paper/main.tex", self.HEAD + "\\bibliography{refs}")
        self.write("paper/refs.bib", "")
        self.assertEqual(self.gather()["bib_files"], ["paper/refs.bib"])

    def test_empty_and_no_root_have_no_bib_files(self):
        self.assertEqual(self.gather()["bib_files"], [])
        self.write("x.tex", "\\bibliography{refs}")
        self.write("refs.bib", "")
        source = self.gather()
        self.assertEqual((source["status"], source["bib_files"]), ("no_root_file", []))

    def test_bib_paths_are_readable_from_source_dir(self):
        self.write("latex/custom.bib", "@misc{k,}")
        for path in self.bibs(self.HEAD + "\\bibliography{latex/custom}"):
            self.assertTrue((self.root / path).is_file())


class ContractTest(GatherTestCase):
    def test_keys_order_types_and_json(self):
        self.write("main.tex", "\\documentclass{article}")
        self.write("main.bbl", "\\begin{thebibliography}{1}\\end{thebibliography}")
        self.write("extra/Refs.BBL", "")
        source = self.gather()
        self.assertEqual(tuple(source), GATHERED_SOURCE_KEYS)
        self.assertEqual(source["bbl_files"], ["extra/Refs.BBL", "main.bbl"])
        self.assertFalse(any("\\" in path or Path(path).is_absolute() for path in source["bbl_files"]))
        self.assertIs(type(source["version"]), int)
        self.assertEqual(json.loads(json.dumps(source)), source)

    def test_invalid_arguments_raise_value_error(self):
        for arxiv_id, version in [("2409.03594v1", 1), ("bad", 1), (ID, 0), (ID, True), (ID, "1")]:
            with self.subTest(arxiv_id=arxiv_id, version=version):
                with self.assertRaises(ValueError):
                    gather_source(self.root, arxiv_id, version)
        with self.assertRaises(ValueError):
            gather_source(self.write("file.tex", "x"), ID, 1)


class CliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        raw = self.base / "corpus" / "raw" / "huynh-nhi"
        (raw / "2409-03594" / "v1").mkdir(parents=True)
        (raw / "2409-03594" / "v1" / "main.tex").write_text("\\documentclass{article}")
        (raw / "2409-03594" / "v2").mkdir()
        (raw / "2409-03594" / "v2" / "source_manifest.json").write_text(
            json.dumps({"arxiv_id": "2409.03594", "version": 2}))
        (raw / "2409-03595" / "v1").mkdir(parents=True)
        (raw / "2409-03595" / "v1" / "notes.tex").write_text("no class")

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli_main(list(argv))
        return code, out.getvalue()

    def test_batch_writes_one_record_per_version(self):
        output = self.base / "out" / "gathered.jsonl"
        code, printed = self.run_cli("batch", "--corpus", str(self.base / "corpus"),
                                     "--owner", "huynh-nhi", "--output", str(output))
        self.assertEqual(code, 0)
        records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(r["arxiv_id"], r["version"], r["status"]) for r in records], [
            ("2409.03594", 1, "ok"),
            ("2409.03594", 2, "empty_source"),
            ("2409.03595", 1, "no_root_file"),
        ])
        self.assertIn("ok: 1", printed)

    def test_batch_limit(self):
        output = self.base / "limited.jsonl"
        self.run_cli("batch", "--corpus", str(self.base / "corpus"), "--owner", "huynh-nhi",
                     "--output", str(output), "--limit", "1")
        self.assertEqual(len(output.read_text(encoding="utf-8").splitlines()), 1)

    def test_batch_diagnostics_lists_versions_to_inspect(self):
        raw = self.base / "corpus" / "raw" / "huynh-nhi" / "2409-03596" / "v1"
        raw.mkdir(parents=True)
        (raw / "main.tex").write_text("\\documentclass{article}\\bibliography{ghost}")
        output, diagnostics = self.base / "g.jsonl", self.base / "d.jsonl"
        self.run_cli("batch", "--corpus", str(self.base / "corpus"), "--owner", "huynh-nhi",
                     "--output", str(output), "--diagnostics", str(diagnostics))
        rows = [json.loads(x) for x in diagnostics.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(r["arxiv_id"], r["missing_bib"]) for r in rows],
                         [("2409.03596", ["ghost"])])

    def test_one_writes_json(self):
        output = self.base / "one.json"
        code, _ = self.run_cli("one", "--source-dir",
                               str(self.base / "corpus/raw/huynh-nhi/2409-03594/v1"),
                               "--arxiv-id", ID, "--version", "1", "--output", str(output))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["root_file"], "main.tex")

    def test_one_missing_dir_fails(self):
        code, _ = self.run_cli("one", "--source-dir", str(self.base / "nope"),
                               "--arxiv-id", ID, "--version", "1")
        self.assertEqual(code, 1)

    def test_folder_name_fallback_for_old_ids(self):
        folder = self.base / "hep-th__9901001"
        folder.mkdir()
        self.assertEqual(paper_id_from_folder(folder), "hep-th/9901001")
        self.assertEqual(paper_id_from_folder(self.base / "2409-03594"), "2409.03594")


if __name__ == "__main__":
    unittest.main()
