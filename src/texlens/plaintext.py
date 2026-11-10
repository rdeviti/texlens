"""Plain text from LaTeX, such as an abstract to paste into a submission
form."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .config import Config, macro_spec
from .latex import definitions, match_braces, read_group
from .source import CodeText, Document, split_comment
from .words import BUILTIN_IGNORE

# Macros dropped with their arguments: name -> mandatory arguments.
DROPPED = {
    "cite": 1, "citep": 1, "citet": 1, "citealp": 1, "citeauthor": 1,
    "citeyear": 1, "nocite": 1, "label": 1, "footnote": 1, "footnotetext": 1,
    "footnotemark": 0, "thanks": 1, "index": 1, "vspace": 1, "hspace": 1,
    "includegraphics": 1, "begin": 1, "end": 1,
}
# Macros that print nothing.
SILENT = {
    "xspace", "noindent", "par", "smallskip", "medskip", "bigskip", "newline",
    "linebreak", "nobreak", "relax", "ignorespaces", "unskip", "protect",
    "centering", "item", "maketitle",
}
SYMBOLS = {
    "LaTeX": "LaTeX", "TeX": "TeX", "ldots": "...", "dots": "...",
    "textendash": "–", "textemdash": "—", "textquoteleft": "‘",
    "textquoteright": "’", "textbackslash": "\\", "ss": "ß", "o": "ø",
    "O": "Ø", "aa": "å", "AA": "Å", "ae": "æ", "AE": "Æ", "oe": "œ",
    "OE": "Œ", "l": "ł", "L": "Ł", "i": "ı", "textregistered": "®",
    "texttrademark": "™", "copyright": "©", "S": "§", "P": "¶",
}
# Formatting macros, which print the text of their (last) argument.
FORMATTING = {
    "emph", "textit", "textbf", "texttt", "textsc", "textsf", "textrm",
    "textup", "textmd", "textsl", "textnormal", "underline", "uline", "mbox",
    "text", "fbox", "enquote", "textcolor", "colorbox", "href",
}
KEEP_LAST = {"textcolor": 2, "colorbox": 2, "href": 2}  # argument count
ACCENTS = {
    "'": "\u0301", "`": "\u0300", "^": "\u0302", '"': "\u0308",
    "~": "\u0303", "=": "\u0304", ".": "\u0307", "u": "\u0306",
    "v": "\u030c", "H": "\u030b", "c": "\u0327", "k": "\u0328",
    "r": "\u030a",
}
ESCAPES = {"%": "%", "&": "&", "$": "$", "#": "#", "_": "_", "{": "{",
           "}": "}", " ": " ", ",": " ", ";": " ", ":": " ", "!": "",
           "@": "", "/": "", "-": "", "\\": " "}

_CONTROL_WORD = re.compile(r"\\([A-Za-z@]+)(\*?)")
_SIMPLE_MATH = re.compile(r"[^\\^_{}]*")


@dataclass
class _Context:
    dropped: dict[str, int]
    changes: set[str]
    macros: dict           # name -> Definition, for expansion
    braces: dict[int, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    warned: set[str] = field(default_factory=set)

    def warn(self, key: str, message: str):
        if key not in self.warned:
            self.warned.add(key)
            self.warnings.append(message)


def _skip_spaces(text: str, i: int) -> int:
    while i < len(text) and text[i] in " \t\n":
        i += 1
    return i


def _arguments(text: str, i: int, count: int, optional: int = 0):
    """Read up to `optional` [...] arguments and `count` {...} arguments.
    Returns (optional ones, mandatory ones, position after them); a missing
    mandatory argument ends the reading."""
    options, args = [], []
    while len(options) < optional or optional < 0:
        group = read_group(text, i, "[")
        if group is None:
            break
        options.append(group[0])
        i = group[1]
    for _ in range(count):
        group = read_group(text, i, "{")
        if group is None:
            break
        args.append(group[0])
        i = group[1]
    return options, args, i


def convert(text: str, ctx: _Context, depth: int = 0) -> str:
    """Plain text of a piece of LaTeX (without comments)."""
    if depth > 20:
        ctx.warn("depth", "macros nest too deeply; output is incomplete")
        return ""
    out, i = [], 0
    while i < len(text):
        c = text[i]
        if c == "\\":
            m = _CONTROL_WORD.match(text, i)
            if m is None:  # a control symbol, such as \% or \'
                symbol = text[i + 1:i + 2]
                if symbol in ACCENTS and symbol not in ESCAPES:
                    i = _accent(text, i + 2, symbol, out, ctx, depth)
                else:
                    out.append(ESCAPES.get(symbol, symbol))
                    i += 2
                continue
            i = _macro(text, m, out, ctx, depth)
        elif c == "{":
            end = read_group(text, i, "{")
            if end is None:
                i += 1
                continue
            out.append(convert(end[0], ctx, depth + 1))
            i = end[1]
        elif c == "}":
            i += 1
        elif c == "$":
            i = _math(text, i, out, ctx)
        elif c == "~":
            out.append(" ")
            i += 1
        elif text.startswith("---", i):
            out.append("—")
            i += 3
        elif text.startswith("--", i):
            out.append("–")
            i += 2
        elif text.startswith("``", i):
            out.append("“")
            i += 2
        elif text.startswith("''", i):
            out.append("”")
            i += 2
        elif c == "`":
            out.append("‘")
            i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _accent(text, i, symbol, out, ctx, depth) -> int:
    """\\'e or \\'{e}: the letter with its accent."""
    i = _skip_spaces(text, i)
    group = read_group(text, i, "{")
    if group is not None:
        letter, i = convert(group[0], ctx, depth + 1), group[1]
    else:
        letter, i = text[i:i + 1], i + 1
    out.append(unicodedata.normalize("NFC", letter + ACCENTS[symbol]))
    return i


def _math(text, i, out, ctx) -> int:
    end = text.find("$", i + 1)
    if end < 0:
        out.append("$")
        return i + 1
    content = text[i + 1:end]
    if _SIMPLE_MATH.fullmatch(content):
        out.append(content)
    else:
        out.append(text[i:end + 1])
        ctx.warn("math", "math with commands is kept as LaTeX, as in "
                 f"{text[i:end + 1]}")
    return end + 1


def _macro(text, m, out, ctx, depth) -> int:
    name, i = m.group(1), m.end()
    if name in BUILTIN_IGNORE:
        ctx.warn(name, f"\\{name}: dropped, but LaTeX prints a reference "
                 "there; edit the text around it")
    if name in ctx.dropped:
        return _arguments(text, i, ctx.dropped[name], optional=-1)[2]
    if name in ctx.changes:
        _, args, i = _arguments(text, i, 1, optional=-1)
        out.append(convert(args[0], ctx, depth + 1) if args else "")
        return i
    if name in ctx.macros:
        d = ctx.macros[name]
        options, args, i = _arguments(text, i, d.args, d.optional)
        if not d.args and text.startswith("{}", i):
            i += 2  # as in \sys{}
        params = (options + [""] * d.optional)[:d.optional] + args
        body = re.sub(r"#(\d)", lambda p: params[int(p.group(1)) - 1]
                      if int(p.group(1)) <= len(params) else "", d.body)
        out.append(convert(body, ctx, depth + 1))
        return i
    if name in SYMBOLS:
        out.append(SYMBOLS[name])
        return i + 2 if text.startswith("{}", i) else i
    if name in SILENT:
        return i
    if name in ACCENTS:  # \c{c}, \v{c}, ...
        return _accent(text, i, name, out, ctx, depth)
    if name in ("url", "path"):
        _, args, i = _arguments(text, i, 1)
        out.append(args[0] if args else "")
        return i
    j = _skip_spaces(text, i)
    if j < len(text) and text[j] in "{[":
        _, args, k = _arguments(text, j, KEEP_LAST.get(name, 1), optional=-1)
        if name in FORMATTING:
            out.append(convert(args[-1], ctx, depth + 1) if args else "")
            return k
        if args and args[-1].strip():
            out.append(convert(args[-1], ctx, depth + 1))
            ctx.warn(name, f"\\{name}: unknown macro, kept the text of its "
                     "argument")
            return k
        i = k
    out.append("\\" + name)
    ctx.warn(name, f"\\{name}: unknown macro, kept as it is")
    return i


def _tidy(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r" +([.,;:!?)\]])", r"\1", text)  # after a dropped \cite
    return re.sub(r"([(\[]) +", r"\1", text)


_ABSTRACT_ENV = re.compile(r"\\begin\s*\{abstract\}(.*?)\\end\s*\{abstract\}",
                           re.S)
_ABSTRACT_MACRO = re.compile(r"\\abstract\s*(?=\{)")


class NoAbstractError(Exception):
    pass


def _lines(code: CodeText, start: int, end: int) -> str:
    """The code between two positions, joined as TeX joins lines: a line
    that ends in a % comment runs into the next one, and only empty lines
    (not lines with just a comment) separate paragraphs."""
    out = []
    for k, line in enumerate(code.lines):
        lo = code.starts[k]
        hi = code.starts[k + 1] - 1 if k + 1 < len(code.lines) \
            else len(code.text)
        if hi < start or lo > end:
            continue
        segment = code.text[max(lo, start):min(hi, end)]
        commented = not line.opaque and split_comment(line.text)[1] != ""
        if commented and not segment.strip():
            continue  # a line with just a comment
        out.append(segment + ("" if commented else "\n"))
    return "".join(out)


@dataclass
class Abstract:
    text: str
    file: str
    line: int
    warnings: list[str]

    def words(self) -> int:
        return len(self.text.split())


def abstract(doc: Document, cfg: Config) -> Abstract:
    """The abstract of the paper as plain text: hidden text and notes are
    left out, the paper's simple macros are expanded, and formatting is
    dropped. Paragraphs are separated by blank lines."""
    code = doc.body()
    masked = code.masked
    m = _ABSTRACT_ENV.search(masked)
    if m:
        start, end = m.span(1)
    else:
        m = _ABSTRACT_MACRO.search(masked)
        group = read_group(masked, m.end(), "{", match_braces(masked)) \
            if m else None
        if group is None:
            raise NoAbstractError("no abstract found: texlens looks for "
                                  "\\begin{abstract} ... \\end{abstract} "
                                  "or \\abstract{...}")
        end = group[1] - 1
        start = end - len(group[0])
    preamble = doc.preamble().masked
    dropped = dict(DROPPED)
    for spec in cfg.notes + cfg.ignore:
        name, n = macro_spec(spec)
        dropped[name] = n
    dropped.update(BUILTIN_IGNORE)
    ctx = _Context(dropped=dropped,
                   changes={macro_spec(s)[0] for s in cfg.changes},
                   macros={d.name: d for d in definitions(preamble)
                           if d.body is not None})
    paragraphs = re.split(r"\n[ \t]*\n", _lines(code, start, end))
    text = "\n\n".join(p for p in (_tidy(convert(p, ctx))
                                   for p in paragraphs) if p)
    file, line = code.where(start)
    return Abstract(text, file, line, ctx.warnings)
