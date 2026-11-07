import re
import unittest

from texlens.source import find_main, load, split_comment

from .util import TESTS, ProjectCase


def visible(doc) -> str:
    """The compiled code as one string: comments removed, spaces collapsed."""
    text = " ".join(line.text if line.opaque else split_comment(line.text)[0]
                    for line in doc.lines)
    return re.sub(r"\s+", " ", text).strip()


class SplitCommentTest(unittest.TestCase):
    def test_plain_comment(self):
        self.assertEqual(split_comment("a % b"), ("a ", "% b"))

    def test_escaped_percent(self):
        self.assertEqual(split_comment(r"50\% done % note"),
                         (r"50\% done ", "% note"))

    def test_escaped_backslash_before_percent(self):
        self.assertEqual(split_comment(r"a\\% b"), (r"a\\", "% b"))

    def test_percent_in_inline_verbatim(self):
        self.assertEqual(split_comment(r"\url{a%20b} c % d"),
                         (r"\url{a%20b} c ", "% d"))
        self.assertEqual(split_comment(r"\verb|%| x"), (r"\verb|%| x", ""))
        # \\ is a line break, so this is not \verb.
        self.assertEqual(split_comment(r"\\verb|%|"), (r"\\verb|", "%|"))


class InputTest(ProjectCase):
    def test_follows_inputs_and_keeps_provenance(self):
        main = self.project({
            "main.tex": "\\begin{document}\nA\n\\input{a}\nD\n"
                        "\\end{document}\n",
            "a.tex": "B\n\\input{sub/c.tex}\n",
            "sub/c.tex": "C\n",
        })
        doc = load(main)
        self.assertEqual(visible(doc),
                         r"\begin{document} A B C D \end{document}")
        where = {split_comment(l.text)[0].strip(): (l.file, l.lineno)
                 for l in doc.lines}
        self.assertEqual(where["C"], ("sub/c.tex", 1))
        self.assertEqual(where["D"], ("main.tex", 4))

    def test_bare_input_and_include(self):
        main = self.project({"main.tex": "\\input a\n\\include{b}\n",
                             "a.tex": "A\n", "b.tex": "B\n"})
        self.assertEqual(visible(load(main)), "A B")

    def test_includeonly(self):
        main = self.project({
            "main.tex": "\\includeonly{b}\n\\include{a}\n\\include{b}\n",
            "a.tex": "A\n", "b.tex": "B\n"})
        self.assertEqual(visible(load(main)), r"\includeonly{b} B")

    def test_subfile_keeps_only_the_body(self):
        main = self.project({
            "main.tex": "X\n\\subfile{part}\n",
            "part.tex": "\\documentclass[main]{subfiles}\n"
                        "\\begin{document}\nP\n\\end{document}\n"})
        self.assertEqual(visible(load(main)), "X P")

    def test_import_and_subimport(self):
        main = self.project({
            "main.tex": "\\import{chap/}{one}\n",
            "chap/one.tex": "1\n\\subimport{sec/}{two}\n\\input{three}\n",
            "chap/sec/two.tex": "2\n",
            "chap/three.tex": "3\n"})
        self.assertEqual(visible(load(main)), "1 2 3")

    def test_missing_file_is_reported(self):
        doc = load(self.project({"main.tex": "A\n\\input{nope}\n"}))
        self.assertEqual(visible(doc), "A")
        self.assertIn("cannot find 'nope'", doc.warnings[0])

    def test_input_in_hidden_text_is_not_followed(self):
        doc = load(self.project({"main.tex": "\\iffalse\n\\input{nope}\n"
                                             "\\fi\nA\n"}))
        self.assertEqual(visible(doc), "A")
        self.assertEqual(doc.warnings, [])

    def test_commented_input_is_not_followed(self):
        doc = load(self.project({"main.tex": "% \\input{nope}\nA\n"}))
        self.assertEqual(doc.warnings, [])

    def test_self_include_is_skipped(self):
        doc = load(self.project({"main.tex": "A\n\\input{main}\n"}))
        self.assertEqual(visible(doc), "A")
        self.assertIn("includes itself", doc.warnings[0])

    def test_deep_includes_are_cut(self):
        files = {"main.tex": "\\input{f0}\n"}
        for i in range(40):
            files[f"f{i}.tex"] = f"{i}\n\\input{{f{i + 1}}}\n"
        doc = load(self.project(files))
        self.assertIn("more than 32 nested files", doc.warnings[0])

    def test_input_prefers_tex_files(self):
        main = self.project({"main.tex": "\\input{a}\n", "a": "plain\n",
                             "a.tex": "tex\n"})
        self.assertEqual(visible(load(main)), "tex")

    def test_include_reads_only_tex_files(self):
        doc = load(self.project({"main.tex": "\\include{b}\n", "b": "B\n"}))
        self.assertEqual(visible(doc), "")
        self.assertIn("cannot find 'b'", doc.warnings[0])

    def test_standalone_document_keeps_only_its_body(self):
        main = self.project({
            "main.tex": "X\n\\input{fig}\n",
            "fig.tex": "% a figure\n\\documentclass{standalone}\n"
                       "\\begin{document}\nP\n\\end{document}\n"})
        self.assertEqual(visible(load(main)), "X P")

    def test_files_outside_the_main_directory(self):
        main = self.project({"paper/main.tex": "\\input{../shared/x}\n",
                             "shared/x.tex": "\\input{nope}\n"})
        doc = load(main.parent / "paper" / "main.tex")
        self.assertIn("../shared/x.tex:1: cannot find", doc.warnings[0])

    def test_line_endings_and_encodings(self):
        main = self.project({"main.tex": "\\input{a}\n\\input{b}\n"})
        (main.parent / "a.tex").write_bytes(b"A1\r\nA2\rA3\x0cA4\n")
        (main.parent / "b.tex").write_bytes(b"caf\xe9\n")
        doc = load(main)
        self.assertEqual([(l.text, l.lineno) for l in doc.lines],
                         [("A1", 1), ("A2", 2), ("A3\x0cA4", 3),
                          ("caf\xe9", 1)])
        self.assertEqual(doc.warnings,
                         ["b.tex: not valid UTF-8; read as Latin-1"])

    def test_blank_lines_are_kept(self):
        doc = load(self.project({"main.tex": "A\n\n\\iffalse\n\nB\n\\fi\n"
                                             "C\n"}))
        self.assertEqual([l.text for l in doc.lines], ["A", "", "C"])


class ConditionalTest(ProjectCase):
    def check(self, source, expected, **options):
        doc = load(self.project({"main.tex": source}), **options)
        self.assertEqual(visible(doc), expected)
        return doc

    def test_if_zero_keeps_the_else_branch(self):
        self.check("\\if 0\nA\n\\else\nB\n\\fi\nC\n", "B C")
        self.check("\\if0 A \\else B \\fi C\n", "B C")

    def test_iffalse_and_iftrue(self):
        self.check("\\iffalse A \\fi B \\iftrue C \\else D \\fi\n", "B C")

    def test_nested_conditionals(self):
        self.check("\\iffalse A \\ifx\\a\\b B \\else C \\fi D \\fi E\n", "E")
        self.check("\\ifx\\a\\b A \\iffalse B \\fi C \\fi\n",
                   r"\ifx\a\b A C \fi")

    def test_newif_switches(self):
        self.check("\\newif\\ifdraft\n\\drafttrue\n"
                   "\\ifdraft A\\else B\\fi\n",
                   r"\newif\ifdraft \drafttrue A")
        self.check("\\newif\\ifdraft\n\\ifdraft A\\else B\\fi\n",
                   r"\newif\ifdraft B")

    def test_switch_set_inside_a_definition_is_ignored(self):
        self.check("\\newif\\ifdraft\\drafttrue\n"
                   "\\newcommand{\\final}{\\draftfalse}\n"
                   "\\ifdraft A\\fi\n",
                   r"\newif\ifdraft\drafttrue "
                   r"\newcommand{\final}{\draftfalse} A")

    def test_configured_conditionals(self):
        self.check("\\iftr A\\else B\\fi\n", "B", false_ifs=["iftr"])
        self.check("\\iftr A\\else B\\fi\n", "A", true_ifs=["iftr"])

    def test_unknown_conditionals_are_kept(self):
        self.check("\\ifpdf A\\else B\\fi\n", r"\ifpdf A\else B\fi")

    def test_etoolbox_macros_are_not_conditionals(self):
        self.check("\\iffalse \\ifstrempty{x}{a}{b} A \\fi B\n", "B")
        self.check("\\iffalse \\ifmytest{x} A \\fi B\n", "B")

    def test_iff_is_not_a_conditional(self):
        self.check("\\iffalse $a \\iff b$ \\fi C\n", "C")

    def test_conditionals_in_comments_are_ignored(self):
        self.check("A % \\iffalse\nB\n", "A B")

    def test_comment_environment(self):
        self.check("A\n\\begin{comment}\nB\n\\end{comment}\nC\n", "A C")

    def test_comment_package_reads_whole_lines(self):
        # The rest of the \begin line is dropped, and only a line with just
        # \end{comment} ends the environment.
        self.check("A \\begin{comment} B \\end{comment} C\nD\n"
                   "  \\end{comment}\nE\n\\end{comment}  \nF\n", "A F")

    def test_comment_environment_of_the_verbatim_package(self):
        self.check("\\usepackage{verbatim}\nA \\begin{comment} B\nC\n"
                   "D \\end{comment} E\nF\n", r"\usepackage{verbatim} A F")

    def test_custom_hidden_environment(self):
        self.check("A\n\\begin{draftonly}\nB\n\\end{draftonly}\nC\n", "A C",
                   hidden_envs=["draftonly"])
        self.check("\\excludecomment{old}\nA\n\\begin{old}\nB\n\\end{old}\n"
                   "C\n", r"\excludecomment{old} A C")

    def test_unclosed_hidden_environment_is_reported(self):
        doc = self.check("A\n\\begin{comment}\nB\n", "A")
        self.assertIn("main.tex:2: \\begin{comment} has no line with just "
                      "\\end{comment}", doc.warnings[0])

    def test_verbatim_is_not_parsed(self):
        self.check("\\begin{verbatim}\n\\iffalse\n\\end{verbatim}\nC\n",
                   r"\begin{verbatim} \iffalse \end{verbatim} C")
        self.check("\\begin{filecontents*}{x.bib}\n@misc{a, url={a%20{b}}\n"
                   "\\end{filecontents*}\n\\newif\\ifdraft\\drafttrue\n"
                   "\\ifdraft A\\fi\n",
                   r"\begin{filecontents*} {x.bib} @misc{a, url={a%20{b}} "
                   r"\end{filecontents*} \newif\ifdraft\drafttrue A")

    def test_verbatim_ends_within_a_line(self):
        doc = self.check("\\begin{verbatim}x\\end{verbatim}"
                         "\\iffalse A\\fi B\n",
                         r"\begin{verbatim} x \end{verbatim} B")
        self.assertEqual([l.opaque for l in doc.lines], [False, True, False])

    def test_inline_verbatim_is_not_parsed(self):
        self.check("\\verb|\\iffalse| A\n", r"\verb|\iffalse| A")
        self.check("\\newif\\ifdraft\n\\url{a%20b}\\drafttrue\n"
                   "\\ifdraft A\\fi\n",
                   r"\newif\ifdraft \url{a%20b}\drafttrue A")

    def test_unclosed_conditional_is_reported(self):
        doc = self.check("A \\iffalse B\n", "A")
        self.assertIn("never closed", doc.warnings[0])

    def test_unclosed_conditional_ends_with_its_file(self):
        # Like LaTeX, which adds a \fi at the end of the file.
        main = self.project({"main.tex": "A\n\\input{part}\nC\n",
                             "part.tex": "B\n\\iffalse\nold\n"})
        doc = load(main)
        self.assertEqual(visible(doc), "A B C")
        self.assertEqual(doc.warnings, [
            "part.tex:2: \\iffalse has no \\fi in part.tex; like LaTeX, "
            "texlens ends it at the end of the file"])

    def test_conditionals_in_definitions_are_not_evaluated(self):
        for definition in (r"\newcommand{\hide}{\iffalse}",
                           r"\def\ifempty#1{\ifx#1\empty}",
                           r"\newenvironment{hide}{\iffalse}{\fi}",
                           r"\NewDocumentCommand\x{O{\iffalse}m}{\iffalse}",
                           r"\newcommand\x[1][{\iffalse}]{\iffalse}"):
            doc = self.check(definition + "\nA\n", definition + " A")
            self.assertEqual(doc.warnings, [])

    def test_let_switches(self):
        self.check("\\let\\ifdraft\\iffalse\n\\ifdraft A\\else B\\fi\n",
                   r"\let\ifdraft\iffalse B")

    def test_unless(self):
        self.check("\\unless\\iffalse A\\fi \\unless\\iftrue B\\fi\n", "A")

    def test_if_compares_two_characters(self):
        # \if 1 compares 1 with the end of the line (a space): false.
        self.check("\\if 1\nA\n\\fi\n\\if 11B\\fi\n\\if 1 1C\\fi\n", "B")

    def test_declared_conditional_with_arguments(self):
        # The ifdraft package's \ifdraft{draft text}{final text}.
        doc = self.check("\\ifdraft{A}{B} C\n", r"\ifdraft{A}{B} C",
                         false_ifs=["ifdraft"])
        self.assertIn("takes arguments", doc.warnings[0])

    def test_macros_with_options_are_not_conditionals(self):
        self.check("\\iffalse \\ifoot[x]{y} A \\fi B\n", "B")

    def test_unclosed_brace_ends_with_its_file(self):
        main = self.project({"main.tex": "\\newif\\ifdraft\n\\input{a}\n"
                                         "\\drafttrue\n\\ifdraft A\\fi\n",
                             "a.tex": "{\n"})
        doc = load(main)
        self.assertEqual(visible(doc), r"\newif\ifdraft { \drafttrue A")
        self.assertEqual(doc.warnings,
                         ["a.tex: 1 unclosed brace(s) at the end of the file"])


class FindMainTest(ProjectCase):
    def test_prefers_main_tex(self):
        main = self.project({"main.tex": "x", "other.tex": "y"})
        self.assertEqual(find_main(str(main.parent)), main)

    def test_finds_the_only_documentclass(self):
        main = self.project({"paper.tex": "\\documentclass{article}\n",
                             "intro.tex": "text\n"})
        self.assertEqual(find_main(str(main.parent)).name, "paper.tex")

    def test_fixture(self):
        self.assertEqual(find_main(str(TESTS / "paper")).name, "main.tex")

    def test_skips_directories(self):
        main = self.project({"paper.tex": "\\documentclass{article}\n",
                             "old.tex/notes.txt": "x\n"})
        self.assertEqual(find_main(str(main.parent)).name, "paper.tex")


class DocumentTest(ProjectCase):
    def test_find_and_slice(self):
        doc = load(TESTS / "paper" / "main.tex")
        start = doc.find([r"\begin{abstract}"], doc.begin_document())
        stop = doc.find([r"\appendix", r"\bibliographystyle"], start)
        text = " ".join(l.text for l in doc.slice(start, stop))
        self.assertTrue(text.startswith(r"\begin{abstract}"))
        self.assertIn("fifteen", text)
        self.assertNotIn("Appendix", text)
        self.assertNotIn("hidden", text)

    def test_begin_document_with_spaces(self):
        doc = load(self.project({"main.tex": "\\title{T}\n\\begin {document}"
                                             "\nA\n\\end {document}\nB\n"}))
        self.assertEqual(doc.body().text.split(), ["A"])


if __name__ == "__main__":
    unittest.main()
