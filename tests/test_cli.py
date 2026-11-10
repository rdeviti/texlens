import contextlib
import io
import json
import unittest

from texlens.cli import main

from .util import TESTS, ProjectCase, needs_texcount, needs_toml

PAPER = str(TESTS / "paper")
MACROS = ["--note", "fixme", "--change", "rev", "--word", "sys=1"]


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class CliTest(ProjectCase):
    def test_notes(self):
        code, out, _ = run("notes", PAPER, *MACROS)
        self.assertEqual(code, 0)
        self.assertIn("main.tex:22  [1 Intro]  \\fixme", out)
        self.assertIn("sections/method.tex:6  [2 Method]  \\todo", out)
        self.assertIn("2 notes", out)

    def test_totals_use_the_singular_for_one(self):
        code, out, _ = run("notes", PAPER)
        self.assertEqual(code, 0)
        self.assertIn("1 note (\\todo: 1)", out)

    def test_changes_summary_and_json(self):
        code, out, _ = run("changes", PAPER, "--summary", *MACROS)
        self.assertEqual(code, 0)
        self.assertIn("1 Intro: 1 change, about 2 words", out)
        code, out, _ = run("changes", PAPER, "--json", *MACROS)
        self.assertEqual(json.loads(out)["changes"][1]["text"],
                         "thirteen fourteen")

    def test_json_has_the_warnings(self):
        main = self.project({"main.tex": "\\todo{a}\n\\iffalse\n"})
        code, out, err = run("notes", str(main), "--json")
        result = json.loads(out)
        self.assertEqual((code, result["notes"][0]["text"]), (0, "a"))
        self.assertIn("main.tex:2: \\iffalse is never closed",
                      result["warnings"][0])
        self.assertIn("warning: main.tex:2: \\iffalse is never closed", err)

    def test_contradicting_settings(self):
        code, _, err = run("macros", PAPER, "--change", "todo")
        self.assertEqual(code, 2)
        self.assertIn("\\todo is in both notes and changes", err)

    def test_keep_does_not_overwrite_the_paper(self):
        main = TESTS / "paper" / "main.tex"
        code, _, err = run("words", PAPER, "--keep", str(main))
        self.assertEqual(code, 2)
        self.assertIn("is a file of the paper", err)

    def test_changes_without_macros(self):
        code, _, err = run("changes", PAPER)
        self.assertEqual(code, 2)
        self.assertIn("no change macros configured", err)

    def test_macros(self):
        code, out, _ = run("macros", PAPER, *MACROS)
        self.assertEqual(code, 0)
        self.assertIn("counts as 1 word", out)

    def test_missing_main(self):
        code, _, err = run("macros", str(TESTS / "nope"))
        self.assertEqual(code, 2)
        self.assertIn("no such file", err)

    def test_missing_texcount(self):
        code, _, err = run("words", PAPER, "--texcount", "no-such-texcount")
        self.assertEqual(code, 2)
        self.assertIn("not found", err)

    @needs_texcount
    def test_words(self):
        code, out, _ = run("words", PAPER, *MACROS)
        self.assertEqual(code, 0)
        self.assertIn("Word count:", out)
        self.assertRegex(out, r"Word count:\s+31")
        code, out, _ = run("words", PAPER, "--json", "--captions", *MACROS)
        self.assertEqual(json.loads(out)["words"]["total"], 32)

    def test_abstract(self):
        code, out, err = run("abstract", PAPER)
        self.assertEqual((code, out), (0, "Alpha beta gamma. We present Foo, "
                                          "a tiny paper—for “testing”.\n"))
        self.assertIn("(10 words, 61 characters)", err)
        self.assertIn("warning: formatting removed; add it back if the form "
                      "accepts it: \\emph{tiny}", err)
        code, out, _ = run("abstract", PAPER, "--json")
        result = json.loads(out)
        self.assertTrue(result["abstract"].startswith("Alpha"))
        self.assertIn("\\emph{tiny}", result["warnings"][0])

    def test_abstract_with_options(self):
        main = self.project({"main.tex": (
            "\\newif\\iffinal\n\\begin{document}\n\\begin{abstract}\n"
            "A \\ask{why?} \\new{B}\\iffinal{} C\\fi.\n\\end{abstract}\n"
            "\\end{document}\n")})
        code, out, err = run("abstract", str(main), "--note", "ask",
                             "--change", "new", "--true-if", "iffinal")
        self.assertEqual((code, out, err.strip()), (0, "A B C.\n",
                                                    "(3 words, 6 characters)"))

    def test_no_abstract(self):
        main = self.project({"main.tex": "\\begin{document}\nA\n"
                                         "\\end{document}\n"})
        code, out, err = run("abstract", str(main))
        self.assertEqual((code, out), (2, ""))
        self.assertIn("no abstract found", err)

    @needs_texcount
    @needs_toml
    def test_words_with_config_file(self):
        main = self.project({
            "main.tex": "\\begin{document}\n\\begin{abstract}\nA \\note{x y}"
                        " \\name\n\\end{abstract}\n\\end{document}\n",
            "texlens.toml": '[macros]\nnotes = ["note"]\n'
                            '[macros.words]\nname = 2\n'})
        code, out, _ = run("words", str(main), "--json")
        # A (1) + \name (2) + the abstract's title (1); \note is ignored.
        self.assertEqual(json.loads(out)["words"]["total"], 4)


if __name__ == "__main__":
    unittest.main()
