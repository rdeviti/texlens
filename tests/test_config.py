import argparse
import unittest

from texlens.config import (Config, ConfigError, apply_overrides,
                            find_config, load_config, macro_spec, validate)

from .util import ProjectCase, needs_toml


class DefaultsTest(unittest.TestCase):
    def test_defaults(self):
        cfg = Config()
        self.assertEqual(cfg.notes, ["todo"])
        self.assertEqual(cfg.hidden_environments, ["comment"])
        self.assertTrue(cfg.footnotes)
        self.assertFalse(cfg.captions or cfg.math)

    def test_macro_spec(self):
        self.assertEqual(macro_spec("note"), ("note", 1))
        self.assertEqual(macro_spec("crefrange:2"), ("crefrange", 2))


@needs_toml
class FileTest(ProjectCase):
    def load(self, text):
        main = self.project({"main.tex": "", "texlens.toml": text})
        return load_config(find_config(main, None))

    def test_full_file(self):
        cfg = self.load(
            '[macros]\nnotes = ["fixme", "\\\\todo"]\nchanges = ["rev"]\n'
            'ignore = ["myref:2"]\n'
            '[macros.words]\nsys = 1\n'
            '[macros.rules]\nreplaced = "[text,ignore]"\n'
            '[conditionals]\nfalse = ["ifdraft"]\n'
            'hidden_environments = ["comment", "notes"]\n'
            '[count]\nstart = ["\\\\maketitle"]\nfootnotes = false\n')
        self.assertEqual(cfg.notes, ["todo", "fixme"])  # adds to defaults
        self.assertEqual(cfg.changes, ["rev"])
        self.assertEqual(cfg.ignore, ["myref:2"])
        self.assertEqual(cfg.words, {"sys": 1})
        self.assertEqual(cfg.rules, {"replaced": "[text,ignore]"})
        self.assertEqual(cfg.false_ifs, ["ifdraft"])
        self.assertEqual(cfg.hidden_environments, ["comment", "notes"])
        self.assertEqual(cfg.start, [r"\maketitle"])
        self.assertFalse(cfg.footnotes)

    def test_unknown_key(self):
        with self.assertRaisesRegex(ConfigError, "unknown key"):
            self.load('[macros]\ncomments = ["fixme"]\n')

    def test_unknown_section(self):
        with self.assertRaisesRegex(ConfigError, "unknown section"):
            self.load('[words]\nfootnotes = true\n')

    def test_bad_values(self):
        with self.assertRaisesRegex(ConfigError, "list of strings"):
            self.load('[macros]\nnotes = "fixme"\n')
        with self.assertRaisesRegex(ConfigError, "conditional"):
            self.load('[conditionals]\nfalse = ["draft"]\n')
        with self.assertRaisesRegex(ConfigError, "true or false"):
            self.load('[count]\nmath = "yes"\n')
        with self.assertRaisesRegex(ConfigError, "word counts"):
            self.load('[macros.words]\nsys = true\n')
        with self.assertRaisesRegex(ConfigError, "invalid macro name"):
            self.load('[macros.words]\n"sys:1" = 1\n')

    def test_no_file(self):
        main = self.project({"main.tex": ""})
        self.assertIsNone(find_config(main, None))
        with self.assertRaises(ConfigError):
            find_config(main, str(main.parent / "missing.toml"))


class OverrideTest(unittest.TestCase):
    def test_command_line_overrides(self):
        args = argparse.Namespace(
            note=["fixme"], change=["rev"], ignore=None, word=["sys=1"],
            false_if=["\\ifdraft"], true_if=None, start=None,
            stop=[r"\appendix"], footnotes=False, captions=None, math=True)
        cfg = apply_overrides(Config(), args)
        self.assertEqual(cfg.notes, ["todo", "fixme"])
        self.assertEqual(cfg.changes, ["rev"])
        self.assertEqual(cfg.words, {"sys": 1})
        self.assertEqual(cfg.false_ifs, ["ifdraft"])
        self.assertEqual(cfg.stop, [r"\appendix"])
        self.assertFalse(cfg.footnotes)
        self.assertTrue(cfg.math)
        self.assertFalse(cfg.captions)

    def test_bad_word_option(self):
        args = argparse.Namespace(word=["sys"])
        with self.assertRaises(ConfigError):
            apply_overrides(Config(), args)

    def test_argument_count_replaces_the_default(self):
        cfg = apply_overrides(Config(), argparse.Namespace(note=["todo:2"]))
        self.assertEqual(cfg.notes, ["todo:2"])


class ValidateTest(unittest.TestCase):
    def test_contradictions(self):
        with self.assertRaisesRegex(ConfigError, "both notes and changes"):
            validate(Config(changes=["todo"]))
        with self.assertRaisesRegex(ConfigError, "both ignore and rules"):
            validate(Config(ignore=["x:2"], rules={"x": "[text]"}))
        with self.assertRaisesRegex(ConfigError, "both false and true"):
            validate(Config(false_ifs=["ifx"], true_ifs=["ifx"]))
        validate(Config(ignore=["x"], words={"x": 1}))  # compatible


if __name__ == "__main__":
    unittest.main()
