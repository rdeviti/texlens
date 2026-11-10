import unittest

from texlens.config import Config
from texlens.plaintext import NoAbstractError, abstract
from texlens.source import load

from .util import ProjectCase

PREAMBLE = (
    "\\newcommand{\\sys}{\\textsc{Foo}\\xspace}\n"
    "\\newcommand{\\rev}[1]{\\textcolor{blue}{#1}}\n"
    "\\newcommand{\\fixme}[1]{\\textcolor{red}{#1}}\n"
    "\\newcommand{\\pair}[2]{#2 and #1}\n"
)


class AbstractTest(ProjectCase):
    def abstract(self, text, cfg=None, preamble=PREAMBLE):
        main = self.project({"main.tex": preamble + "\\begin{document}\n"
                             "\\begin{abstract}\n" + text
                             + "\n\\end{abstract}\n\\end{document}\n"})
        return abstract(load(main), cfg or Config(notes=["fixme"],
                                                  changes=["rev"]))

    def check(self, text, expected, **kwargs):
        result = self.abstract(text, **kwargs)
        self.assertEqual(result.text, expected)
        return result

    def test_macros(self):
        self.check("We present \\sys, \\emph{a} \\textit{tool} \\rev{that "
                   "\\textbf{works}}.\\fixme{shorten} \\pair{x}{y}.",
                   "We present Foo, a tool that works. y and x.")

    def test_citations_and_references(self):
        result = self.check("Known~\\cite{a,b}. See \\cref{sec:x}.",
                            "Known. See.")
        self.assertIn("\\cref: dropped", result.warnings[0])

    def test_characters(self):
        self.check("caf\\'e, na\\\"{\\i}ve, \\c{c}a --- ``quoted'' -- "
                   "50\\% \\& more\\ldots",
                   "café, naı̈ve, ça — “quoted” – 50% & more...")

    def test_math(self):
        result = self.check("Cost $O(n)$ for $k$ and $\\epsilon$.",
                            "Cost O(n) for k and $\\epsilon$.")
        self.assertIn("math with commands", result.warnings[0])

    def test_unknown_macro_is_kept_with_a_warning(self):
        result = self.check("A \\mystery{} b.", "A \\mystery b.")
        self.assertIn("\\mystery: unknown macro", result.warnings[0])

    def test_lines_and_paragraphs(self):
        self.check("One%\ntwo\n% only a comment\nthree.\n\nFour \\url{a_b}.",
                   "Onetwo three.\n\nFour a_b.")

    def test_hidden_text(self):
        self.check("A\n\\iffalse old \\fi\nB.", "A B.")

    def test_abstract_macro(self):
        main = self.project({"main.tex": "\\abstract{Short \\emph{one}.}\n"})
        self.assertEqual(abstract(load(main), Config()).text, "Short one.")

    def test_no_abstract(self):
        main = self.project({"main.tex": "Text.\n"})
        with self.assertRaises(NoAbstractError):
            abstract(load(main), Config())


if __name__ == "__main__":
    unittest.main()
