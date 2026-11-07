"""Command-line interface."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import textwrap
from collections import Counter
from pathlib import Path

from . import __version__
from .config import (CONFIG_NAME, ConfigError, apply_overrides, find_config,
                     load_config, validate)
from .listing import changes, macros, notes
from .source import find_main, load
from .words import TeXcountError, count_words


def _common(parser: argparse.ArgumentParser):
    parser.add_argument("main", nargs="?", help="main .tex file or its "
                        "directory (default: main.tex, or the only .tex file "
                        "with \\documentclass in the current directory)")
    parser.add_argument("--config", metavar="FILE",
                        help=f"settings file (default: {CONFIG_NAME} next to "
                        "the main file)")
    group = parser.add_argument_group("macros (add to the settings file)")
    group.add_argument("--note", action="append", metavar="NAME",
                       help="a macro for notes to coauthors, without the "
                       "backslash, e.g. fixme for \\fixme (\\todo is one by "
                       "default); repeatable")
    group.add_argument("--change", action="append", metavar="NAME",
                       help="a macro marking revised text, e.g. rev for "
                       "\\rev; repeatable")
    group.add_argument("--ignore", action="append", metavar="NAME",
                       help="a macro whose arguments are not words; use "
                       "NAME:N for N arguments; repeatable")
    group.add_argument("--word", action="append", metavar="NAME=N",
                       help="a macro that prints N words, e.g. sys=1; "
                       "repeatable")
    group.add_argument("--false-if", action="append", metavar="NAME",
                       help="a conditional to treat as false, e.g. iffinal")
    group.add_argument("--true-if", action="append", metavar="NAME",
                       help="a conditional to treat as true")
    parser.add_argument("--json", action="store_true",
                        help="print machine-readable JSON")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="texlens",
        description="A lens on your TeX, without the clutter: count the "
        "words of your paper, and list its notes to coauthors and revised "
        "passages. Text that never reaches the PDF (\\iffalse, \\if 0, false "
        "\\newif switches, comment environments) is left out.")
    parser.add_argument("--version", action="version",
                        version=f"texlens {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="command")
    sub.required = True

    words = sub.add_parser("words", help="count the words of the abstract "
                           "and main text (needs TeXcount)",
                           description="Count the words of the abstract and "
                           "main text, excluding references, appendices, "
                           "captions, and formulas. TeXcount does the "
                           "counting, on the text without its hidden parts.")
    _common(words)
    words.add_argument("--start", action="append", metavar="MARKER",
                       help="where counting starts, e.g. '\\maketitle' "
                       "(default: '\\begin{abstract}'); repeatable")
    words.add_argument("--stop", action="append", metavar="MARKER",
                       help="where counting stops (default: the first of "
                       "\\appendix, \\bibliography, ...); repeatable")
    words.add_argument("--captions", action="store_true", default=None,
                       help="also count figure and table captions")
    words.add_argument("--math", action="store_true", default=None,
                       help="count each formula as one word")
    words.add_argument("--no-footnotes", dest="footnotes",
                       action="store_false", default=None,
                       help="do not count footnotes")
    words.add_argument("--texcount", metavar="CMD",
                       help="TeXcount command (default: $TEXLENS_TEXCOUNT "
                       "or texcount)")
    words.add_argument("--keep", metavar="FILE",
                       help="save the flattened text given to TeXcount")

    for name, text in (("notes", "list notes to coauthors (note macros) "
                        "outside hidden text"),
                       ("changes", "list revised passages (change macros) "
                        "outside hidden text")):
        p = sub.add_parser(name, help=text, description=text.capitalize()
                           + ".")
        _common(p)
        p.add_argument("--full", action="store_true",
                       help="show complete texts instead of one line each")
        p.add_argument("--summary", action="store_true",
                       help="only show totals per section")

    p = sub.add_parser("macros", help="list the paper's macros and how the "
                       "word count treats them",
                       description="List the macros defined in the project, "
                       "how often the main text uses them, and how the word "
                       "count treats them.")
    _common(p)
    return parser


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):  # print any text the paper has
            stream.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    try:
        return _run(args)
    except BrokenPipeError:  # e.g. texlens comments | head
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except (OSError, ValueError):
            pass
        return 1
    except (ConfigError, TeXcountError) as e:
        print(f"texlens: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        message = (f"{e.filename}: {e.strerror}" if e.filename and e.strerror
                   else str(e))
        print(f"texlens: {message}", file=sys.stderr)
        return 2


def _run(args) -> int:
    main_file = find_main(args.main)
    cfg = load_config(find_config(main_file, args.config))
    cfg = validate(apply_overrides(cfg, args))
    doc = load(main_file, cfg.false_ifs, cfg.true_ifs,
               cfg.hidden_environments)
    if args.command == "words":
        return _words(doc, cfg, args)
    if args.command in ("notes", "changes"):
        return _entries(doc, cfg, args)
    return _macros(doc, cfg, args)


def _warn(warnings):
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)


def _print_json(command: str, result, warnings):
    print(json.dumps({command: result, "warnings": warnings}, indent=2))


def _words(doc, cfg, args) -> int:
    result = count_words(doc, cfg, args.texcount, args.keep)
    _warn(result.warnings)
    if args.json:
        counts = result.as_dict()
        del counts["warnings"]
        _print_json("words", counts, result.warnings)
        return 0
    print(f"Counted from {result.start} to {result.stop}.")
    rows = [("Words in text", result.text, True),
            ("Words in headings", result.headers, True),
            ("Words in captions", result.captions, cfg.captions),
            ("Formulas", result.inline_math + result.display_math, cfg.math)]
    for label, value, counted in rows:
        note = "" if counted else "  (not counted)"
        print(f"  {label + ':':<20}{value:>8}{note}")
    print(f"  {'Word count:':<20}{result.total:>8}")
    return 0


def _entries(doc, cfg, args) -> int:
    kind = args.command
    names = cfg.notes if kind == "notes" else cfg.changes
    if not names:
        print(f"texlens: no {kind[:-1]} macros configured; use "
              f"--{kind[:-1]} NAME or [macros] {kind} in {CONFIG_NAME}",
              file=sys.stderr)
        return 2
    found = notes(doc, cfg) if kind == "notes" else changes(doc, cfg)
    _warn(doc.warnings)
    if args.json:
        _print_json(kind, [e.as_dict() for e in found], doc.warnings)
        return 0
    if args.summary:
        _summary(found, kind)
        return 0
    width = shutil.get_terminal_size((100, 20)).columns
    for e in found:
        print(f"{e.file}:{e.line}  [{e.section}]  \\{e.macro}")
        if args.full:
            print(textwrap.indent(textwrap.fill(e.text, width - 4), "    "))
        else:
            print("    " + textwrap.shorten(e.text, width - 4,
                                            placeholder=" ..."))
    _summary(found, kind, totals_only=True)
    return 0


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _summary(found, kind, totals_only=False):
    noun = kind[:-1]  # note or change
    per_macro = Counter(e.macro for e in found)
    macros_text = ", ".join(f"\\{m}: {n}" for m, n in per_macro.items())
    print(_count(len(found), noun) + (f" ({macros_text})" if found else ""))
    if totals_only:
        return
    sections = {}
    for e in found:
        n, w = sections.get(e.section, (0, 0))
        sections[e.section] = (n + 1, w + e.words)
    for section, (n, w) in sections.items():
        print(f"  {section}: {_count(n, noun)}, about {_count(w, 'word')}")


def _macros(doc, cfg, args) -> int:
    found = macros(doc, cfg)
    _warn(doc.warnings)
    if args.json:
        _print_json("macros", [m.as_dict() for m in found], doc.warnings)
        return 0
    if not found:
        print("No macros defined in the project.")
        return 0
    name_width = min(max(len(m.name) for m in found), 24) + 2
    print(f"{'macro':<{name_width}}{'args':>5}{'uses':>7}  treatment")
    for m in found:
        args_text = f"{m.args}" + (f"+{m.optional}" if m.optional else "")
        print(f"\\{m.name:<{name_width - 1}}{args_text:>5}{m.uses:>7}  "
              f"{m.treatment}")
    print(f"\nConfigure macros in {CONFIG_NAME} next to "
          f"{Path(doc.main).name}, or with "
          f"--note/--change/--ignore/--word.")
    return 0
