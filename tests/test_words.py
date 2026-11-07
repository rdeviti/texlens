import re
import subprocess
import unittest

from texlens.config import Config
from texlens.source import Line, load
from texlens.words import (count_words, directive, flatten, ignore_warnings,
                           rules, texcount_command)

from .util import TESTS, ProjectCase, needs_texcount

PAPER = TESTS / "paper" / "main.tex"


def paper_config(**changes) -> Config:
    cfg = Config(notes=["todo", "fixme"], changes=["rev"], words={"sys": 1})
    for key, value in changes.items():
        setattr(cfg, key, value)
    return cfg


class FlattenTest(ProjectCase):
    def test_range_and_rules(self):
        text, _, start, stop = flatten(load(PAPER), paper_config())
        self.assertEqual((start, stop), (r"\begin{abstract}",
                                         r"\bibliographystyle"))
        self.assertIn(r"%TC:macro \fixme [ignore]", text)
        self.assertIn(r"%TC:macro \rev [text]", text)
        self.assertIn(r"%TC:macroword \sys 1", text)
        self.assertIn(r"%TC:macro \cref [ignore]", text)
        self.assertIn(r"%TC:macro \footnote [text]", text)
        self.assertNotIn("hidden", text)
        self.assertNotIn("not shown", text)
        self.assertNotIn("Appendix", text)
        self.assertIn("fifteen", text)

    def test_rules_for_multiple_arguments(self):
        lines = rules(Config(ignore=["myref:2"], rules={"x": "[text,ignore]"},
                             footnotes=False))
        self.assertIn(r"%TC:macro \myref [ignore,ignore]", lines)
        self.assertIn(r"%TC:macro \footnote [ignore]", lines)
        self.assertIn(r"%TC:macro \footnotetext [ignore]", lines)
        self.assertIn(r"%TC:envir CCSXML [] xall", lines)
        self.assertEqual(lines[-1], r"%TC:macro \x [text,ignore]")

    def test_directive(self):
        for comment in ("%TC:ignore", "%%tc: Ignore  % note", "%TC:ignore x"):
            self.assertEqual(directive(comment), "ignore")
        for comment in ("% TC:ignore", "%TCignore", "% note"):
            self.assertIsNone(directive(comment))

    def test_preamble_settings(self):
        main = self.project({"main.tex": (
            "\\documentclass{article}\n"
            "\\usepackage[table]{xcolor}\\usepackage{hyperref , url}\n"
            "%TC:macroword \\name 2\n%TC:ignore\n%TC:endignore\n"
            "\\begin{document}\nA\n\\end{document}\n")})
        text, *_ = flatten(load(main), Config())
        header = text.split("\\begin{document}")[0]
        self.assertIn("\\usepackage{xcolor,hyperref,url}\n", header)
        self.assertIn("%TC:macroword \\name 2\n", header)
        self.assertNotIn("%TC:ignore", header)
        self.assertNotIn("%TC:endignore", header)

    def test_inline_verbatim_is_emptied(self):
        main = self.project({"main.tex": (
            "\\begin{document}\nSee \\url{a%20b} and \\verb|%x| "
            "\\lstinline{x = y}.\n\\end{document}\n")})
        text, *_ = flatten(load(main), Config())
        self.assertIn(r"See \url{} and \verb|%x| \lstinline{}.", text)

    def test_unclosed_ignore_before_the_stop_is_closed(self):
        main = self.project({"main.tex": "\\begin{document}\nA\n"
                                         "%TC:ignore\n\\bibliography{x}\n"
                                         "\\end{document}\n"})
        text, *_ = flatten(load(main), Config())
        self.assertIn("%TC:endignore\n\\end{document}", text)

    def test_ignore_warning(self):
        lines = [Line(t, "f.tex", i) for i, t in enumerate([
            "%TC:ignore",
            "This paragraph has many words that the author did not mean "
            "to hide from the count, because the marker was placed too "
            "early before the figure that follows it.",
            "\\begin{figure}\\caption{x}\\end{figure}",
            "%TC:endignore"], 1)]
        warnings = ignore_warnings(lines)
        self.assertEqual(len(warnings), 1)
        self.assertIn("f.tex:1", warnings[0])
        only_figure = [lines[0], lines[2], lines[3]]
        self.assertEqual(ignore_warnings(only_figure), [])


@needs_texcount
class CountTest(unittest.TestCase):
    """Expected counts (see tests/paper):
    abstract 3 (Alpha beta gamma); Intro: One two Foo three (4), four five
    (2), See (1), six (1), Footnote seven eight (3); Method: thirteen
    fourteen (2), fifteen (1); Math here (2); twelve (1) -> 20 in text;
    headings Abstract (TeXcount counts its title), Intro, Method -> 3;
    caption "eleven" -> 1; one formula."""

    def count(self, **changes):
        return count_words(load(PAPER), paper_config(**changes))

    def test_default(self):
        result = self.count()
        self.assertEqual((result.text, result.headers, result.total),
                         (20, 3, 23))
        self.assertEqual(result.captions, 1)
        self.assertEqual(result.inline_math, 1)
        self.assertEqual(result.warnings, [])

    def test_options(self):
        self.assertEqual(self.count(captions=True).total, 24)
        self.assertEqual(self.count(math=True).total, 24)
        self.assertEqual(self.count(footnotes=False).total, 21)

    def test_unconfigured_macros(self):
        result = count_words(load(PAPER), Config())
        # \fixme's argument (3) is counted, \sys is not (-1): 23 + 3 - 1.
        self.assertEqual(result.total, 25)


@needs_texcount
class TeXcountComparisonTest(ProjectCase):
    def test_same_count_as_texcount_without_hidden_text(self):
        # TeXcount needs the preamble's packages and %TC: lines for this.
        main = self.project({"main.tex": (
            "\\documentclass{article}\n\\usepackage{xcolor,hyperref}\n"
            "%TC:macroword \\sys 1\n\\newcommand{\\sys}{Foo}\n"
            "\\begin{document}\n"
            "One \\textcolor{red}{two three} \\sys{} \\url{http://a.b/c}\n"
            "\\href{http://a.b}{four}.\n\n\\section{Five}\nSix.\n"
            "\\end{document}\n")})
        out = subprocess.run(texcount_command() + ["-total", str(main)],
                             capture_output=True, encoding="utf-8").stdout
        expected = dict(re.findall(r"^Words in (text|headers): (\d+)", out,
                                   re.M))
        result = count_words(load(main), Config())
        # One two three Foo four Six, and the URL, which TeXcount counts as
        # one word when hyperref is loaded.
        self.assertEqual((result.text, result.headers), (7, 1))
        self.assertEqual((str(result.text), str(result.headers)),
                         (expected["text"], expected["headers"]))

    def test_code_and_metadata_are_not_counted(self):
        main = self.project({"main.tex": (
            "\\begin{document}\nOne\n\\begin{CCSXML}\n<ccs2012>words</ccs2012>"
            "\n\\end{CCSXML}\n\\begin{minted}{python}\nprint('two words')\n"
            "\\end{minted}\nThree \\lstinline|a%b| \\url{x%y} four.\n"
            "\\end{document}\n")})
        result = count_words(load(main), Config())
        self.assertEqual((result.total, result.warnings), (3, []))

    def test_front_matter_is_not_counted(self):
        main = self.project({"main.tex": (
            "\\begin{document}\n\\begin{abstract}\nOne two.\n\\end{abstract}\n"
            "\\ccsdesc[500]{Human-centered computing~Ubiquitous computing}\n"
            "\\keywords{location privacy, sensing}\n"
            "\\received{20 February 2007}\n"
            "\\received[revised]{12 March 2009}\n"
            "\\begin{teaserfigure}\n\\caption{Teaser words}\n"
            "\\end{teaserfigure}\n\\maketitle\nThree.\n\\end{document}\n")})
        result = count_words(load(main), Config())
        # The teaser's caption counts as a caption; "Abstract" as a heading.
        self.assertEqual((result.text, result.headers, result.captions),
                         (3, 1, 2))


class KeepTest(ProjectCase):
    def test_keep_does_not_overwrite_the_paper(self):
        main = self.project({"main.tex": "A\n"})
        with self.assertRaisesRegex(FileExistsError, "is a file of the "
                                                     "paper"):
            count_words(load(main), Config(), keep=main)


if __name__ == "__main__":
    unittest.main()
