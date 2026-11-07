"""Notes to coauthors, revision markup, and the paper's own macros."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass

from .config import Config, macro_spec
from .latex import (definitions, find_calls, headings, plain, section_at,
                    simple_macros, word_estimate)
from .source import Document
from .words import BUILTIN_IGNORE


@dataclass
class Entry:
    macro: str
    file: str
    line: int
    section: str
    text: str
    words: int

    def as_dict(self) -> dict:
        return asdict(self)


def entries(doc: Document, specs: list[str]) -> list[Entry]:
    """Calls of the given macros in the compiled body, outermost only."""
    code = doc.body()
    simple = simple_macros(doc.preamble().masked)
    heads = headings(code, simple)
    wanted = dict(macro_spec(s) for s in specs)
    out = []
    for call in find_calls(code.masked, wanted, source=code.text):
        file, line = code.where(call.start)
        text = " | ".join(plain(a, simple) for a in call.args)
        out.append(Entry(call.name, file, line,
                         section_at(heads, call.start), text,
                         sum(word_estimate(a) for a in call.args)))
    return out


def notes(doc: Document, cfg: Config) -> list[Entry]:
    return entries(doc, cfg.notes)


def changes(doc: Document, cfg: Config) -> list[Entry]:
    return entries(doc, cfg.changes)


@dataclass
class Macro:
    name: str
    args: int
    optional: int
    uses: int
    file: str
    line: int
    treatment: str

    def as_dict(self) -> dict:
        return asdict(self)


_CONTROL_WORD = re.compile(r"\\\\|\\([A-Za-z@]+)")


def macros(doc: Document, cfg: Config) -> list[Macro]:
    """Macros defined in the project, how often the body uses them, and how
    the word count treats them."""
    everything = doc.code((0, 0), (len(doc.lines), 0))
    uses = Counter(m.group(1) for m in
                   _CONTROL_WORD.finditer(doc.body().masked) if m.group(1))
    found: dict[str, Macro] = {}
    for d in definitions(everything.masked):
        if d.name not in found:
            file, line = everything.where(d.pos)
            found[d.name] = Macro(d.name, d.args, d.optional, uses[d.name],
                                  file, line, treatment(d.name, d.args, cfg))
    return sorted(found.values(), key=lambda mac: (-mac.uses, mac.name))


def treatment(name: str, args: int, cfg: Config) -> str:
    """How the word count treats \\name, a macro with `args` mandatory
    arguments."""
    def names(specs):
        return {macro_spec(s)[0] for s in specs}

    words = cfg.words.get(name)
    counted = (f"counts as {words} word{'s' if words != 1 else ''}"
               if words is not None else "")
    if name in cfg.rules:
        rule = f"TeXcount rule {cfg.rules[name]}"
    elif name in names(cfg.notes):
        rule = "note: not counted"
    elif name in names(cfg.changes):
        rule = "change: counted"
    elif name in names(cfg.ignore) or name in BUILTIN_IGNORE:
        rule = "arguments not counted"
    elif counted:
        return counted + (", plus its arguments" if args else "")
    elif args:
        return "arguments counted as text"
    else:
        return "no words counted"
    return f"{counted}; {rule}" if counted else rule
