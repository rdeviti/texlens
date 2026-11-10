import unittest

from texlens.config import Config
from texlens.plaintext import NoAbstractError, abstract
from texlens.source import load

from .util import ProjectCase

PREAMBLE = (
    "\\newcommand{\\sys}{\\textsc{Foo}\\xspace}\n"
    "\\newcommand{\\rev}[1]{\\textcolor{blue}{#1}}\n"
    "\\newcommand{\\fixme}[1]{\\textcolor{red}{Fix: #1}}\n"
    "\\newcommand{\\pair}[2]{#2 and #1}\n"
)
NOTES = Config(notes=["todo", "fixme"], changes=["rev"])
FORMATTING = "formatting removed; add it back if the form accepts it: "


class AbstractCase(ProjectCase):
    def abstract(self, text, cfg=None, preamble=PREAMBLE, files=None):
        main = self.project({"main.tex": preamble + "\\begin{document}\n"
                             "\\begin{abstract}\n" + text
                             + "\n\\end{abstract}\n\\end{document}\n",
                             **(files or {})})
        cfg = cfg or NOTES
        return abstract(load(main, cfg.false_ifs, cfg.true_ifs,
                             cfg.hidden_environments), cfg)

    def check(self, text, expected, warnings=None, **kwargs):
        """Check the text of an abstract, and its warnings if given."""
        result = self.abstract(text, **kwargs)
        self.assertEqual(result.text, expected)
        if warnings is not None:
            self.assertEqual(result.warnings, warnings)
        return result


class MacroTest(AbstractCase):
    def test_paper_macros_are_expanded(self):
        self.check("We present \\sys, and \\sys is fast. \\sys{} works. "
                   "\\pair{x}{y}.",
                   "We present Foo, and Foo is fast. Foo works. y and x.")

    def test_arguments(self):
        preamble = ("\\newcommand{\\pct}[1]{#1\\%}\n"
                    "\\newcommand{\\term}[2][def]{#1-#2}\n"
                    "\\def\\etal{et al.\\xspace}\n"
                    "\\newcommand{\\outer}[1]{<\\inner{#1}>}\n"
                    "\\newcommand{\\inner}[1]{[#1]}\n")
        self.check("\\pct{95} \\pct 5 \\term{a} \\term[b]{c} Smith \\etal{} "
                   "did it. \\outer{x}",
                   "95% 5% def-a b-c Smith et al. did it. <[x]>",
                   warnings=[], preamble=preamble)

    def test_delimited_parameters_are_not_expanded(self):
        result = self.check("A \\pt x. b", "A \\pt x. b",
                            preamble="\\def\\pt#1.{#1}\n")
        self.assertIn("\\pt: its parameters are delimited", result.warnings[0])

    def test_macros_defined_in_other_files_and_in_the_body(self):
        main = self.project({
            "main.tex": "\\input{macros}\n\\begin{document}\n"
                        "\\newcommand{\\late}{Late}\n\\begin{abstract}\n"
                        "\\name{} and \\late.\n\\end{abstract}\n"
                        "\\end{document}\n",
            "macros.tex": "\\newcommand{\\name}{Name}\n"})
        self.assertEqual(abstract(load(main), Config()).text,
                         "Name and Late.")

    def test_runaway_macros_stop(self):
        result = self.check("A \\loop b.", "A b.",
                            preamble="\\def\\loop{\\loop}\n")
        self.assertIn("the text is incomplete", result.warnings[0])
        # 2^25 expansions, if texlens did not stop them.
        preamble = "".join(f"\\def\\m{chr(97 + k)}{{\\m{chr(98 + k)}"
                           f"\\m{chr(98 + k)}}}\n" for k in range(25))
        result = self.abstract("A \\ma b.", preamble=preamble)
        self.assertIn("too many macro expansions", result.warnings[1])

    def test_unknown_macros_are_kept_with_a_warning(self):
        self.check("A \\mystery b \\other{c} d \\none{} e.",
                   "A \\mystery b c d \\none e.",
                   warnings=["\\mystery: unknown macro, kept as it is",
                             "\\other: unknown macro, kept the text of its "
                             "argument",
                             "\\none: unknown macro, kept as it is"])


class NotesAndChangesTest(AbstractCase):
    def test_notes_are_dropped_and_changes_kept(self):
        self.check("A \\rev{new \\rev{text}}.\\fixme{shorten} "
                   "\\todo[inline]{check} B.",
                   "A new text. B.", warnings=[])

    def test_without_settings_macros_print_what_latex_prints(self):
        # \todo is always a note; \fixme prints its definition's text.
        self.check("A \\rev{new}. \\fixme{shorten} \\todo{check}",
                   "A new. Fix: shorten", cfg=Config())

    def test_ignored_macros_are_dropped(self):
        self.check("A \\myref{x} b.", "A b.",
                   cfg=Config(ignore=["myref"]))


class ConditionalTest(AbstractCase):
    def test_hidden_text_is_skipped(self):
        self.check("A\n\\iffalse old \\fi\n\\begin{comment}\nold\n"
                   "\\end{comment}\nB % a comment\nC.", "A B C.")

    def test_switches_in_macros(self):
        preamble = ("\\newif\\ifrevision\\revisiontrue\n\\newif\\iffinal\n"
                    "\\newcommand{\\chg}[1]{\\ifrevision\\textcolor{blue}{#1}"
                    "\\else#1\\fi}\n"
                    "\\newcommand{\\draft}[1]{\\iffinal\\else[#1]\\fi}\n")
        self.check("A \\chg{new} \\draft{draft} word.",
                   "A new [draft] word.", warnings=[], preamble=preamble)

    def test_declared_conditionals_in_macros(self):
        preamble = "\\newcommand{\\x}{\\ifpdf PDF\\else DVI\\fi}\n"
        self.check("\\x.", "DVI.", warnings=[], preamble=preamble,
                   cfg=Config(false_ifs=["ifpdf"]))

    def test_unknown_conditionals(self):
        preamble = ("\\newcommand{\\x}{\\ifcustom One\\else Two\\fi}\n"
                    "\\newcommand{\\y}{\\ifx\\a\\b Three\\fi}\n")
        result = self.check("\\x \\y \\ifthenelse{\\boolean{b}}{Four}{Five}.",
                            "One Four.", preamble=preamble)
        self.assertEqual(len(result.warnings), 3)
        self.assertIn("\\ifcustom: texlens cannot evaluate",
                      result.warnings[0])
        self.assertIn("its text is left out", result.warnings[1])


class DroppedTextTest(AbstractCase):
    def test_citations_references_and_footnotes(self):
        result = self.check("Known~\\cite[p.~3]{a,b}. See \\cref{sec:x} "
                            "and Fig.~\\ref{f}. A\\footnote{note}. "
                            "\\label{abs}Done.",
                            "Known. See and Fig.. A. Done.")
        self.assertEqual([w.split(":")[0] for w in result.warnings],
                         ["\\cite", "\\cref", "\\ref", "\\footnote"])
        self.assertIn("LaTeX prints a reference", result.warnings[1])

    def test_spacing_and_layout_commands(self):
        self.check("\\noindent A\\vspace{1em} b\\hspace*{2pt}c \\mbox{d} "
                   "\\color{red}e\\newline{}f.", "A b c d e f.",
                   warnings=[])


class FormattingTest(AbstractCase):
    def test_formatting_is_removed_and_listed(self):
        self.check("An \\emph{important} \\textit{tool} that "
                   "\\textbf{works}, {\\em really} {\\bfseries well}.",
                   "An important tool that works, really well.",
                   warnings=[FORMATTING + "\\emph{important}, \\textit{tool}, "
                             "\\textbf{works}, {\\em really}, "
                             "{\\bfseries well}"])

    def test_listed_once_and_shortened(self):
        result = self.abstract("\\sys and \\sys. \\emph{a very long "
                               "emphasized text that goes on}.")
        self.assertEqual(result.warnings,
                         [FORMATTING + "\\textsc{Foo}, "
                          "\\emph{a very long emphasized text...}"])

    def test_text_without_formatting_has_no_warning(self):
        self.check("Plain \\textcolor{red}{red} \\href{https://a.b}{link} "
                   "$\\times$ 5.", "Plain red link × 5.", warnings=[])

    def test_nested_formatting(self):
        result = self.check("\\textbf{bold \\emph{both}}", "bold both")
        self.assertEqual(result.warnings,
                         [FORMATTING + "\\emph{both}, \\textbf{bold both}"])


class CharacterTest(AbstractCase):
    def test_accents(self):
        self.check("caf\\'e na\\\"{\\i}ve \\c{c}a r\\^ole \\v s \\ss{} "
                   "\\o{} \\aa", "café naı̈ve ça rôle š ß ø å")

    def test_punctuation(self):
        self.check("1--2 a---b, ``double'' `single' it's~ok 10\\,000 "
                   "50\\% A\\&B \\$5 \\#1 a\\_b \\{x\\} \\ldots{} end\\dots",
                   "1–2 a—b, “double” ‘single’ it’s ok 10 000 50% A&B $5 "
                   "#1 a_b {x} ... end...")

    def test_symbols(self):
        self.check("\\LaTeX{} and \\TeX, 100\\texttimes{} faster, "
                   "\\textasciitilde{}10, 20\\textdegree{}C, \\S 3, "
                   "\\enquote{quoted}.",
                   "LaTeX and TeX, 100× faster, ~10, 20°C, § 3, “quoted”.",
                   warnings=[])

    def test_text_written_in_unicode_is_kept(self):
        self.check("Ünïcödé — “as is” 😀", "Ünïcödé — “as is” 😀")


class MathTest(AbstractCase):
    def test_simple_math_becomes_text(self):
        result = self.check(
            "$k$ users, $O(n^2)$, $10^{-3}$, H$_2$O, $\\sim$10, 5$\\times$, "
            "30$^\\circ$C, $\\epsilon$-DP, $\\mathrm{ms}$, \\(a+b\\), "
            "\\ensuremath{\\alpha}, $\\{1, 2\\}$.",
            "k users, O(n²), 10⁻³, H₂O, ~10, 5×, 30°C, ε-DP, ms, a+b, α, "
            "{1, 2}.")
        # Letters and exponents are formatting; symbols are not.
        self.assertEqual(result.warnings,
                         [FORMATTING + "$k$, $O(n^2)$, $10^{-3}$, $_2$, "
                          "\\(a+b\\)"])

    def test_other_math_stays_latex(self):
        result = self.check("Sum $\\sum_i x_i$ and $\\frac{a}{b}$.",
                            "Sum $\\sum_i x_i$ and $\\frac{a}{b}$.")
        self.assertIn("kept as LaTeX, as in $\\sum_i x_i$",
                      result.warnings[0])

    def test_display_math_and_dollar_signs(self):
        self.check("A $$x$$ b \\[ y \\] costs \\$5 or $5$.",
                   "A x b y costs $5 or 5.")


class LayoutTest(AbstractCase):
    def test_lines_and_paragraphs(self):
        self.check("One%\ntwo\n% only a comment\nthree.\n\n\n"
                   "Four.\\par Five\\\\[2pt] six.",
                   "Onetwo three.\n\nFour.\n\nFive six.")

    def test_urls_and_verbatim(self):
        self.check("See \\url{https://a.b/c_d%20e} and \\verb|x_y| and "
                   "\\href{https://a.b}{our site}.",
                   "See https://a.b/c_d%20e and x_y and our site.",
                   warnings=[])

    def test_broken_latex_does_not_stop_texlens(self):
        for text in ("Unbalanced { brace", "\\emph{unclosed", "a } b",
                     "$ open math", "\\", "\\'", "\\ifx", "\\\\[",
                     "\\begin{itemize}\\item A \\item B\\end{itemize}"):
            with self.subTest(text=text):
                self.abstract(text)

    def test_empty_abstract(self):
        self.check("% nothing yet", "")


class FindTest(ProjectCase):
    def test_abstract_in_an_included_file(self):
        main = self.project({
            "main.tex": "\\begin{document}\n\\input{sections/abstract}\n"
                        "\\end{document}\n",
            "sections/abstract.tex": "% the abstract\n\\begin{abstract}\n"
                                     "Text.\n\\end{abstract}\n"})
        result = abstract(load(main), Config())
        self.assertEqual((result.text, result.file, result.line),
                         ("Text.", "sections/abstract.tex", 2))

    def test_abstract_macro_in_the_preamble(self):
        # As in the MDPI template.
        main = self.project({"main.tex": (
            "\\documentclass{article}\n\\abstract{Short \\emph{one}.}\n"
            "\\begin{document}\nText.\n\\end{document}\n")})
        self.assertEqual(abstract(load(main), Config()).text, "Short one.")

    def test_commented_abstract_is_skipped(self):
        main = self.project({"main.tex": (
            "\\begin{document}\n%\\begin{abstract}Old.\\end{abstract}\n"
            "\\iffalse\n\\begin{abstract}Older.\\end{abstract}\n\\fi\n"
            "\\begin{abstract}New.\\end{abstract}\n\\end{document}\n")})
        self.assertEqual(abstract(load(main), Config()).text, "New.")

    def test_no_abstract(self):
        main = self.project({"main.tex": "Text.\n"})
        with self.assertRaises(NoAbstractError):
            abstract(load(main), Config())


class RealisticTest(AbstractCase):
    """An abstract as written in a paper under revision."""

    def test_acm_style_abstract(self):
        preamble = (
            "\\documentclass[acmsmall]{acmart}\n"
            "\\usepackage{xspace}\n"
            "\\newcommand{\\sys}{\\textsc{Spot}\\xspace}\n"
            "\\newcommand{\\eg}{e.g.,\\xspace}\n"
            "\\newif\\ifrevision\\revisiontrue\n"
            "\\newcommand{\\rev}[1]{\\ifrevision{\\color{blue}#1}"
            "\\else#1\\fi}\n"
            "\\newcommand{\\fixme}[1]{\\textcolor{red}{FIXME: #1}}\n")
        text = (
            "Location proofs let users prove where they were~\\cite{a,b}.\n"
            "We present \\sys, a system that verifies claims with\n"
            "\\emph{witnesses}---nearby devices---\\rev{without trusted\n"
            "infrastructure}.%\n"
            "\\fixme{too long?}\n"
            "\\sys costs $O(n)$ messages for $n$ witnesses and works in\n"
            "caf\\'es and ``smart'' buildings (\\eg offices).\n"
            "\n"
            "% Results:\n"
            "In a deployment with 50 users, \\sys reached 95\\% accuracy\n"
            "within 10~m; see \\url{https://example.org/spot}.\n")
        result = self.abstract(text, cfg=Config(notes=["fixme"]),
                               preamble=preamble)
        self.assertEqual(result.text, (
            "Location proofs let users prove where they were. We present "
            "Spot, a system that verifies claims with witnesses—nearby "
            "devices—without trusted infrastructure. Spot costs O(n) "
            "messages for n witnesses and works in cafés and “smart” "
            "buildings (e.g., offices).\n\n"
            "In a deployment with 50 users, Spot reached 95% accuracy within "
            "10 m; see https://example.org/spot."))
        self.assertEqual(result.warnings, [
            "\\cite: dropped the citation; check the text around it",
            FORMATTING + "\\textsc{Spot}, \\emph{witnesses}, $O(n)$, $n$"])
        self.assertEqual(result.words(), 52)


if __name__ == "__main__":
    unittest.main()
