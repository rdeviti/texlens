import unittest

from texlens.config import Config
from texlens.latex import definitions, find_calls, headings, plain, read_group
from texlens.listing import changes, macros, notes
from texlens.source import load

from .util import TESTS, ProjectCase

PAPER = TESTS / "paper" / "main.tex"


class HelpersTest(unittest.TestCase):
    def test_read_group(self):
        self.assertEqual(read_group(r" {a{b}\}c} d", 0), (r"a{b}\}c", 10))
        self.assertIsNone(read_group("x{a}", 0))

    def test_optional_argument_ends_at_the_first_bracket(self):
        self.assertEqual(read_group("[a[b]c]", 0, "["), ("a[b", 5))
        self.assertEqual(read_group("[{a]}b]", 0, "["), ("{a]}b", 7))

    def test_find_calls_skips_options_and_nesting(self):
        calls = find_calls(r"\todo[inline]{a \todo{b}} \todox{c} \todo{d}",
                           {"todo": 1})
        self.assertEqual([c.args for c in calls], [[r"a \todo{b}"], ["d"]])

    def test_find_calls_with_two_arguments(self):
        calls = find_calls(r"\replaced{new}{old}", {"replaced": 2})
        self.assertEqual(calls[0].args, ["new", "old"])

    def test_find_calls_skips_line_breaks(self):
        calls = find_calls(r"a\\todo{b} \todo{c}", {"todo": 1})
        self.assertEqual([c.args for c in calls], [["c"]])

    def test_plain(self):
        self.assertEqual(plain(r"\emph{A}~\textbf{\rev{B}}  {C}"), "A B C")
        self.assertEqual(plain(r"{\sys}: x", {"sys": "Foo"}), "Foo: x")
        self.assertEqual(plain(r"\{x\} 50\% \textcolor{red}{y}"),
                         "{x} 50% red y")

    def test_definitions(self):
        found = {d.name: (d.args, d.optional, d.body) for d in definitions(
            r"\newcommand{\a}[2][x]{A#2} \def\b#1.{B}"
            r"\NewDocumentCommand\c{O{\d} m}{C} \providecommand\e{E}")}
        self.assertEqual(found, {"a": (1, 1, "A#2"), "b": (1, 0, "B"),
                                 "c": (1, 1, "C"), "e": (0, 0, "E")})


class HeadingTest(ProjectCase):
    def test_numbering_with_starred_and_appendix(self):
        main = self.project({"main.tex": (
            "\\begin{document}\n\\section{A}\n\\subsection{B}\n"
            "\\section*{C}\n\\section{D}\n\\subsection[short]{E}\n"
            "\\appendix\n\\section{F}\n\\subsection{G}\n"
            "\\end{document}\n")})
        labels = [h.label() for h in headings(load(main).body())]
        self.assertEqual(labels, ["1 A", "1.1 B", "C", "2 D", "2.1 E",
                                  "A F", "A.1 G"])


class ListingTest(ProjectCase):
    def setUp(self):
        self.doc = load(PAPER)

    def test_notes(self):
        cfg = Config(notes=["todo", "fixme"])
        found = notes(self.doc, cfg)
        self.assertEqual([(e.macro, e.file, e.line, e.section, e.text)
                          for e in found],
                         [("fixme", "main.tex", 22, "1 Intro",
                           "should not count"),
                          ("todo", "sections/method.tex", 6, "2 Method",
                           "a note")])

    def test_hidden_notes_are_not_listed(self):
        main = self.project({"main.tex": (
            "\\begin{document}\n\\iffalse\\todo{x}\\fi\n"
            "\\begin{comment}\n\\todo{y}\n\\end{comment}\n"
            "% \\todo{z}\n\\end{document}\n")})
        self.assertEqual(notes(load(main), Config()), [])

    def test_verbatim_notes_are_not_listed(self):
        main = self.project({"main.tex": (
            "\\begin{document}\n\\verb|\\todo{x}| \\lstinline!\\todo{y}!\n"
            "\\begin{verbatim}\n\\todo{z}\n\\end{verbatim}\n\\todo{real}\n"
            "\\end{document}\n")})
        self.assertEqual([e.text for e in notes(load(main), Config())],
                         ["real"])

    def test_changes_outermost_only(self):
        found = changes(self.doc, Config(changes=["rev"]))
        self.assertEqual([(e.section, e.text, e.words) for e in found],
                         [("1 Intro", "four five", 2),
                          ("2 Method", "thirteen fourteen", 2)])

    def test_sections_around_the_abstract(self):
        main = self.project({"main.tex": (
            "\\newcommand{\\sys}{Foo}\n\\begin{document}\n"
            "\\begin{abstract}\\rev{a}\\end{abstract}\n\\rev{b}\n"
            "\\section{\\sys{} works}\n\\rev{c \\sys}\n\\end{document}\n")})
        found = changes(load(main), Config(changes=["rev"]))
        self.assertEqual([(e.section, e.text) for e in found],
                         [("Abstract", "a"), ("Front matter", "b"),
                          ("1 Foo works", "c Foo")])

    def test_macros(self):
        found = {m.name: m for m in macros(self.doc, Config(
            notes=["todo", "fixme"], changes=["rev"], words={"sys": 1}))}
        self.assertEqual((found["sys"].args, found["sys"].uses,
                          found["sys"].treatment), (0, 1, "counts as 1 word"))
        self.assertEqual(found["rev"].uses, 3)  # one call is nested
        self.assertEqual(found["fixme"].treatment, "note: not counted")
        self.assertEqual(found["finalversion"].treatment, "no words counted")

    def test_macros_with_options_and_parameters(self):
        main = self.project({"main.tex": (
            "\\NewDocumentCommand\\name{O{x}m}{#2}\n\\def\\pt#1.{#1}\n"
            "\\begin{document}\n\\name{a} \\pt b. \\name[c]{d}\n"
            "\\end{document}\n")})
        found = {m.name: m for m in macros(load(main),
                                           Config(words={"name": 2}))}
        self.assertEqual((found["name"].args, found["name"].optional,
                          found["name"].uses), (1, 1, 2))
        self.assertEqual(found["name"].treatment,
                         "counts as 2 words, plus its arguments")
        self.assertEqual((found["pt"].args, found["pt"].uses), (1, 1))


if __name__ == "__main__":
    unittest.main()
