"""Small LaTeX parsing helpers: macro arguments and definitions, headings,
plain text."""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from functools import cached_property

from .source import CodeText, group_end

_BRACE = re.compile(r"\\.|[{}]", re.S)


def match_braces(text: str) -> dict[int, int]:
    """Map the position of each { in `text` to the position after its }."""
    pairs, stack = {}, []
    for m in _BRACE.finditer(text):
        c = m.group(0)
        if c == "{":
            stack.append(m.start())
        elif c == "}" and stack:
            pairs[stack.pop()] = m.end()
    return pairs


def read_group(text: str, pos: int, open_: str = "{",
               braces: dict[int, int] | None = None):
    """If a group starts at `pos` (after spaces), return its content and the
    position after it; otherwise None. As for LaTeX's optional arguments, a
    [...] group ends at the first ] outside braces. `braces`, from
    match_braces(text), saves scanning the text again."""
    i = pos
    while i < len(text) and text[i] in " \t\n":
        i += 1
    if i >= len(text) or text[i] != open_:
        return None
    if open_ == "{":
        end = braces.get(i, -1) if braces is not None else group_end(text, i)
        return (text[i + 1:end - 1], end) if end > 0 else None
    j, limit = i + 1, min(len(text), i + 10000)
    while j < limit:
        c = text[j]
        if c == "\\":
            j += 2
        elif c == "]":
            return text[i + 1:j], j + 1
        elif c == "{":
            j = braces.get(j, -1) if braces is not None else group_end(text, j)
            if j < 0:
                return None
        else:
            j += 1
    return None


@dataclass
class Call:
    name: str
    args: list[str]
    start: int
    end: int


def find_calls(text: str, specs: dict[str, int], source: str | None = None,
               braces: dict[int, int] | None = None) -> list[Call]:
    """Calls of the macros in `specs` (name -> number of mandatory arguments),
    skipping optional [...] arguments. Calls nested in another call are not
    reported separately.

    The arguments are copied from `source` if given: a text with the same
    positions as `text`, such as the code before verbatim was masked."""
    if not specs:
        return []
    source = text if source is None else source
    if braces is None:
        braces = match_braces(text)
    names = sorted(specs, key=len, reverse=True)
    pattern = re.compile(r"\\\\|\\(" + "|".join(map(re.escape, names))
                         + r")(?![A-Za-z@])")
    calls, pos = [], 0
    while True:
        m = pattern.search(text, pos)
        if not m:
            return calls
        if m.group(1) is None:  # \\, as in \\todo
            pos = m.end()
            continue
        name, i, args = m.group(1), m.end(), []
        while True:  # optional arguments
            option = read_group(text, i, "[", braces)
            if option is None:
                break
            i = option[1]
        for _ in range(specs[name]):
            group = read_group(text, i, "{", braces)
            if group is None:
                break
            i = group[1]
            args.append(source[i - 1 - len(group[0]):i - 1])
        if len(args) == specs[name]:
            calls.append(Call(name, args, m.start(), i))
            pos = i
        else:
            pos = m.end()


@dataclass
class Definition:
    name: str
    args: int          # mandatory arguments
    optional: int      # optional arguments
    body: str | None   # None if it could not be read
    pos: int


_DEFINE = re.compile(r"""
    \\\\
  | \\(?:(?:re)?newcommand|providecommand|DeclareRobustCommand
       |(?:re|provide)?newrobustcmd
       |(?P<doc>(?:New|Renew|Provide|Declare)(?:Expandable)?DocumentCommand)
       |(?P<def>[gex]?def))
    \*?\s*(?:\{\s*\\(?P<n1>[A-Za-z@]+)\s*\}|\\(?P<n2>[A-Za-z@]+))
""", re.X)
_ARGUMENT_COUNT = re.compile(r"\s*\[\s*(\d)\s*\]")


def definitions(text: str) -> list[Definition]:
    """The macros that `text` defines with \\newcommand, \\def,
    \\NewDocumentCommand, and similar commands, in order."""
    braces = match_braces(text)
    out = []
    for m in _DEFINE.finditer(text):
        name = m.group("n1") or m.group("n2")
        if name is None:  # \\
            continue
        i, args, optional = m.end(), 0, 0
        if m.group("doc"):
            spec = read_group(text, i, "{", braces)
            if spec is None:
                continue
            args, optional = _xparse_arguments(spec[0])
            i = spec[1]
        elif m.group("def"):
            j = text.find("{", i, i + 200)  # after the parameter text
            if j < 0:
                continue
            args, i = len(re.findall(r"#\d", text[i:j])), j
        else:
            count = _ARGUMENT_COUNT.match(text, i)
            if count:
                args, i = int(count.group(1)), count.end()
                default = read_group(text, i, "[", braces)
                if default is not None and args:
                    args, optional, i = args - 1, 1, default[1]
        body = read_group(text, i, "{", braces)
        out.append(Definition(name, args, optional,
                              body[0] if body else None, m.start()))
    return out


def _xparse_arguments(spec: str) -> tuple[int, int]:
    """(mandatory, optional) arguments of an xparse argument specification,
    such as "O{default} m"."""
    previous = None
    while previous != spec:  # drop {...}: default values and processors
        previous, spec = spec, re.sub(r"\{[^{}]*\}", "", spec)
    spec = re.sub(r"\\[A-Za-z@]+|\\.", "", spec)
    return (len(re.findall(r"[mrRvblu]", spec)),
            len(re.findall(r"[oOdDstgGeE]", spec)))


class SimpleMacros(dict):
    """Macros without arguments that print plain text: name -> text."""

    @cached_property
    def pattern(self) -> re.Pattern | None:
        if not self:
            return None
        names = sorted(self, key=len, reverse=True)
        return re.compile(r"\\(" + "|".join(map(re.escape, names))
                          + r")(?![A-Za-z@])")

    def expand(self, text: str) -> str:
        if self.pattern is None:
            return text
        return self.pattern.sub(lambda m: self[m.group(1)], text)


def simple_macros(text: str) -> SimpleMacros:
    """Macros without arguments whose definition is plain text, such as
    \\newcommand{\\sys}{Foo}."""
    out = SimpleMacros()
    for d in definitions(text):
        if (d.args == d.optional == 0 and d.body is not None
                and not re.search(r"[\\{}#]", d.body)):
            out.setdefault(d.name, d.body.strip())
    return out


_CMD_BEFORE_GROUP = re.compile(r"\\[A-Za-z@]+\*?\s*(?=\{)")
_CLEANUP = re.compile(r"\\\\|\\([{}%&$#_])|\\,|~|[{}]")


def _clean(m) -> str:
    if m.group(1):
        return m.group(1)
    text = m.group(0)
    if text == "\\\\":
        return text
    return " " if text in ("~", "\\,") else ""


def plain(text: str, simple: dict[str, str] | None = None) -> str:
    """Readable one-line version of LaTeX text (light cleanup only): macros
    in `simple` are expanded, and other macros before a {...} group are
    dropped, keeping the group's text."""
    if simple:
        if not isinstance(simple, SimpleMacros):
            simple = SimpleMacros(simple)
        text = simple.expand(text)
    text = _CMD_BEFORE_GROUP.sub("", text).replace("}{", "} {")
    return re.sub(r"\s+", " ", _CLEANUP.sub(_clean, text)).strip()


LEVELS = {"chapter": 0, "section": 1, "subsection": 2, "subsubsection": 3}
_HEADING = re.compile(r"\\(chapter|section|subsection|subsubsection)"
                      r"(?![A-Za-z@])(\*?)|\\appendix(?![A-Za-z@])|"
                      r"\\(begin|end)\s*\{abstract\}")


@dataclass
class Heading:
    pos: int
    number: str
    title: str

    def label(self) -> str:
        return f"{self.number} {self.title}".strip()


def headings(code: CodeText, simple: dict[str, str] | None = None
             ) -> list[Heading]:
    """Headings in order, numbered as LaTeX would (appendices get letters).
    Abstract boundaries are included as pseudo-headings."""
    text = code.masked
    braces = match_braces(text)
    top = 0 if re.search(r"\\chapter(?![A-Za-z@])\*?\s*[\[{]", text) else 1
    counters = {level: 0 for level in LEVELS.values()}
    appendix = False
    out = []
    for m in _HEADING.finditer(text):
        if m.group(0).startswith(r"\appendix"):
            appendix = True
            counters = {level: 0 for level in counters}
            continue
        if m.group(3):  # \begin/\end{abstract}
            out.append(Heading(m.start(), "", "Abstract"
                               if m.group(3) == "begin" else "Front matter"))
            continue
        level = LEVELS[m.group(1)]
        i = m.end()
        short = read_group(text, i, "[", braces)
        if short:
            i = short[1]
        group = read_group(text, i, "{", braces)
        if group is None or level < top:
            continue
        end = group[1]
        title = plain(code.text[end - 1 - len(group[0]):end - 1], simple)
        number = ""
        if not m.group(2):
            counters[level] += 1
            for deeper in range(level + 1, 4):
                counters[deeper] = 0
            parts = []
            for lv in range(top, level + 1):
                value = counters[lv]
                if lv == top and appendix:
                    parts.append(_letter(value))
                else:
                    parts.append(str(value))
            number = ".".join(parts)
        out.append(Heading(m.start(), number, title))
    return out


def _letter(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(ord("A") + r) + s
    return s or "0"


def section_at(heads: list[Heading], pos: int) -> str:
    starts = [h.pos for h in heads]
    i = bisect.bisect_right(starts, pos) - 1
    return heads[i].label() if i >= 0 else "Front matter"


def word_estimate(text: str) -> int:
    """Rough number of words in LaTeX text (for summaries, not the count)."""
    text = re.sub(r"\\[A-Za-z@]+\*?", " ", text)
    text = re.sub(r"\$[^$]*\$", " ", text)
    return len(re.findall(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*", text))
