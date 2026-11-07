"""Read a LaTeX project the way LaTeX reads it.

`load()` starts from the main file, follows \\input, \\include, \\subfile,
\\import and \\subimport, and drops the text that never reaches the PDF:

- false conditionals: \\iffalse, \\if 0, switches defined with \\newif (or
  with \\let to \\iftrue or \\iffalse) and set to false, and conditionals the
  user declares false (or true);
- environments such as `comment`, and those hidden with \\excludecomment.

Conditionals inside macro definitions are not evaluated, since LaTeX runs
them only when the macro is used. Every kept line remembers the file and
line number it came from.
"""

from __future__ import annotations

import bisect
import os
import re
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

# etoolbox-style macros named \if... that take arguments and have no \fi.
NOT_CONDITIONALS = {
    "f", "thenelse", "bool", "notbool", "toggle", "nottoggle", "boolexpr",
    "boolexpe", "def", "undef", "csdef", "csundef", "defmacro", "csmacro",
    "defparam", "csparam", "defprefix", "csprefix", "defprotected",
    "csprotected", "defltxprotect", "csltxprotect", "defempty", "csempty",
    "defvoid", "csvoid", "defequal", "csequal", "defstring", "csstring",
    "defstrequal", "csstrequal", "defcounter", "cscounter", "ltxcounter",
    "deflength", "cslength", "defdimen", "csdimen", "strequal", "strempty",
    "blank", "numcomp", "numequal", "numgreater", "numless", "numodd",
    "dimcomp", "dimequal", "dimgreater", "dimless", "inlist", "inlistcs",
    "rmnum",
}

# Environments whose content is not LaTeX code.
OPAQUE_ENVIRONMENTS = {
    "verbatim", "verbatim*", "Verbatim", "Verbatim*", "BVerbatim",
    "LVerbatim", "VerbatimOut", "lstlisting", "minted", "filecontents",
    "filecontents*",
}

# TeX allows only 15 nested files; this limit guards against include loops.
MAX_INCLUDE_DEPTH = 32

# Inline text that is not LaTeX code: \verb, \lstinline, \mintinline, \url,
# \path, and the URL of \href.
INLINE_VERBATIM = r"""
    \\verb\*?(?P<vd>[^\sA-Za-z*])(?:(?!(?P=vd)).)*(?P=vd)
  | \\lstinline(?![A-Za-z@])\s*(?:\[[^\]]*\])?\s*
      (?:\{[^{}]*\}|(?P<ld>[^\s{\[A-Za-z])(?:(?!(?P=ld)).)*(?P=ld))
  | \\mintinline(?![A-Za-z@])\s*(?:\[[^\]]*\])?\s*\{[^{}]*\}\s*
      (?:\{[^{}]*\}|(?P<md>[^\s{])(?:(?!(?P=md)).)*(?P=md))
  | \\(?:url|path)(?![A-Za-z@])\s*
      (?:\{[^{}]*\}|(?P<ud>[^\s{])(?:(?!(?P=ud)).)*(?P=ud))
  | \\href(?![A-Za-z@])\s*\{[^{}]*\}
"""
# Escaped backslashes come first, so that \\verb is not read as \verb.
_INLINE_VERBATIM = re.compile(r"(?P<pair>\\\\)|" + INLINE_VERBATIM, re.X)
_MAY_HAVE_VERBATIM = re.compile(r"\\(?:verb|lstinline|mintinline|url|path"
                                r"|href)")

# Definition commands, and how many brace groups of LaTeX code (argument
# specifications and bodies) follow the name they define.
_DEFINITION_GROUPS = {"define": 1, "docdefine": 2, "envdefine": 2,
                      "docenvdefine": 3, "def": 1}

_TOKEN = re.compile(r"""
    (?P<verbatim>""" + INLINE_VERBATIM + r""")
  | (?P<define>\\(?:(?:re)?newcommand|providecommand|DeclareRobustCommand
       |(?:re|provide)?newrobustcmd)\*?
       \s*(?:\{\s*\\[A-Za-z@]+\s*\}|\\[A-Za-z@]+))
  | (?P<docdefine>\\(?:New|Renew|Provide|Declare)(?:Expandable)?
       DocumentCommand\s*(?:\{\s*\\[A-Za-z@]+\s*\}|\\[A-Za-z@]+))
  | (?P<envdefine>\\(?:re)?newenvironment\*?\s*\{[^{}]*\})
  | (?P<docenvdefine>\\(?:New|Renew|Provide|Declare)DocumentEnvironment
       \s*\{[^{}]*\})
  | (?P<def>\\[gex]?def\s*\\[A-Za-z@]+)
  | (?P<let>\\let\s*\\(?P<letname>[A-Za-z@]+)\s*=?\s*
       \\(?P<lettarget>[A-Za-z@]+))
  | (?P<newif>\\newif\s*\\if(?P<newname>[A-Za-z@]+))
  | (?P<if>(?P<unless>\\unless\s*)?\\if(?P<ifname>[A-Za-z@]*))
  | (?P<else>\\else(?![A-Za-z@]))
  | (?P<fi>\\fi(?![A-Za-z@]))
  | (?P<set>\\(?P<setname>[A-Za-z@]+?)(?P<setval>true|false)(?![A-Za-z@]))
  | (?P<begin>\\begin\s*\{(?P<benv>[^{}]*)\})
  | (?P<input>\\(?P<icmd>input|include|subfile)\s*\{(?P<ipath>[^{}]*)\})
  | (?P<inputbare>\\input\s+(?P<ibare>[^\s{}\\%]+))
  | (?P<import>\\(?P<isub>sub)?(?:import|inputfrom|includefrom)\s*
       \{(?P<idir>[^{}]*)\}\s*\{(?P<ifile>[^{}]*)\})
  | (?P<includeonly>\\includeonly\s*\{(?P<only>[^{}]*)\})
  | (?P<package>\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*
       \{(?P<packages>[^{}]*)\})
  | (?P<excludecomment>\\excludecomment\s*\{(?P<excluded>[^{}]*)\})
  | (?P<escaped>\\[{}%\\])
  | (?P<lbrace>\{)
  | (?P<rbrace>\})
""", re.X)

BEGIN_DOCUMENT = re.compile(r"\\begin\s*\{document\}")
END_DOCUMENT = re.compile(r"\\end\s*\{document\}")
_DOCUMENTCLASS = re.compile(r"\\documentclass(?![A-Za-z@])")
# What may come before \documentclass in a file.
_BEFORE_CLASS = re.compile(r"\\(?:RequirePackage|PassOptionsToPackage|"
                           r"PassOptionsToClass|DocumentMetadata|pdfoutput)"
                           r"(?![A-Za-z@])")


def _masker(fill: str):
    def replace(m):
        text = m.group(0)
        if m.group("pair"):
            return text
        return "".join("\n" if c == "\n" else fill for c in text)
    return replace


_MASK_NUL = _masker("\0")
_MASK_SPACE = _masker(" ")


def split_comment(line: str) -> tuple[str, str]:
    """Split a line into its code and its comment (from the first real %).

    A % inside inline verbatim text, such as \\verb|%| or \\url{a%20b}, does
    not start a comment."""
    if "%" not in line:
        return line, ""
    search = line
    if _MAY_HAVE_VERBATIM.search(line):
        search = _INLINE_VERBATIM.sub(_MASK_NUL, line)
    start = 0
    while True:
        i = search.find("%", start)
        if i < 0:
            return line, ""
        backslashes = i - len(search[:i].rstrip("\\"))
        if backslashes % 2 == 0:
            return line[:i], line[i:]
        start = i + 1


def mask_inline_verbatim(code: str) -> str:
    """`code` with inline verbatim text, such as \\verb|...|, replaced by
    spaces, so that positions stay the same."""
    if not _MAY_HAVE_VERBATIM.search(code):
        return code
    return _INLINE_VERBATIM.sub(_MASK_SPACE, code)


def _empty(m) -> str:
    text = m.group(0)
    if m.group("pair") or text.startswith("\\verb"):
        return text
    return re.match(r"\\[A-Za-z]+", text).group(0) + "{}"


def empty_inline_verbatim(code: str) -> str:
    """`code` with the arguments of inline verbatim commands emptied, as in
    \\url{}, for TeXcount: it would read a % in a URL as a comment, and the
    code in \\lstinline as words. \\verb, which TeXcount handles, is kept."""
    if not _MAY_HAVE_VERBATIM.search(code):
        return code
    return _INLINE_VERBATIM.sub(_empty, code)


def marker_pattern(markers) -> re.Pattern:
    """Regex for LaTeX markers such as "\\appendix" (whole control words)."""
    return re.compile("|".join(
        re.escape(m) + ("(?![A-Za-z@])" if m[-1:].isalpha() else "")
        for m in markers))


@dataclass
class Line:
    """A line of compiled text: the visible code, plus the comment when the
    line ends in visible text (so that TeXcount instructions survive).
    `opaque` marks the content of verbatim environments."""
    text: str
    file: str
    lineno: int
    opaque: bool = False


@dataclass
class CodeText:
    """Code without comments (and with verbatim environments blanked out),
    and a map back to the source lines."""
    text: str
    starts: list[int]
    lines: list[Line]

    @cached_property
    def masked(self) -> str:
        """`text` with inline verbatim blanked out too: this is the text to
        search for macros. Positions are the same as in `text`."""
        return mask_inline_verbatim(self.text)

    def where(self, pos: int) -> tuple[str, int]:
        if not self.lines:
            return "", 0
        i = max(bisect.bisect_right(self.starts, pos) - 1, 0)
        return self.lines[i].file, self.lines[i].lineno


@dataclass
class Document:
    main: Path
    lines: list[Line]
    warnings: list[str] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)  # all files read

    def search(self, pattern: re.Pattern, start=(0, 0)):
        """First (line, column, match) at or after `start` where `pattern`
        matches code (not comments or verbatim text), or None."""
        first, col = start
        for i in range(first, len(self.lines)):
            line = self.lines[i]
            if line.opaque:
                continue
            code = mask_inline_verbatim(split_comment(line.text)[0])
            m = pattern.search(code, col if i == first else 0)
            if m:
                return i, m.start(), m
        return None

    def find(self, markers, start=(0, 0)):
        """First (line, column) at or after `start` where one of `markers`
        appears in code, or None."""
        if not markers:
            return None
        found = self.search(marker_pattern(markers), start)
        return found[:2] if found else None

    def has_begin_document(self) -> bool:
        return self.search(BEGIN_DOCUMENT) is not None

    def begin_document(self):
        """Position right after \\begin{document} (or the very start)."""
        found = self.search(BEGIN_DOCUMENT)
        if found is None:
            return 0, 0
        line, _, m = found
        return line, m.end()

    def end_document(self):
        """Position of \\end{document} (or the very end)."""
        found = self.search(END_DOCUMENT, self.begin_document())
        return found[:2] if found else (len(self.lines), 0)

    def slice(self, start, end) -> list[Line]:
        """The lines between two (line, column) positions. The last line keeps
        only its code before `end`."""
        (l0, c0), (l1, c1) = start, end
        out = []
        for i in range(l0, min(l1, len(self.lines) - 1) + 1):
            line = self.lines[i]
            text = line.text
            if i == l1:
                text = split_comment(text)[0][:c1]
            if i == l0:
                text = text[c0:]
            out.append(Line(text, line.file, line.lineno, line.opaque))
        return out

    def code(self, start, end) -> CodeText:
        """The code between two positions, without comments, and with the
        content of verbatim environments replaced by spaces."""
        parts, starts, lines, offset = [], [], [], 0
        for line in self.slice(start, end):
            if line.opaque:
                code = " " * len(line.text)
            else:
                code = split_comment(line.text)[0]
            parts.append(code)
            starts.append(offset)
            lines.append(line)
            offset += len(code) + 1
        return CodeText("\n".join(parts), starts, lines)

    def body(self) -> CodeText:
        """The code of the document body, from \\begin{document} on."""
        return self.code(self.begin_document(), self.end_document())

    def preamble(self) -> CodeText:
        return self.code((0, 0), self.begin_document())


@dataclass
class _Frame:
    kind: str      # "false" or "true" (evaluated), or "other" (not evaluated)
    visible: bool
    name: str      # e.g. "\iffalse", for messages
    where: str     # file:line where it was opened


class _Reader:
    def __init__(self, root: Path, false_ifs, true_ifs, hidden_envs):
        self.root = root
        self.false_ifs = {name[2:] for name in false_ifs}
        self.true_ifs = {name[2:] for name in true_ifs}
        self.hidden_envs = set(hidden_envs)
        # Hidden environments defined by the comment package, which ends
        # them only at a line with just \end{name}. Others, such as the
        # comment environment of the verbatim package, end at \end{name}.
        self.line_envs = {"comment"}
        self.stack: list[_Frame] = []
        self.switches: dict[str, bool] = {}
        self.depth = 0
        # A definition being read: how many of its brace groups are still to
        # come, at which brace depth, and whether an optional argument [...]
        # is open.
        self.definition_groups = 0
        self.definition_depth = 0
        self.definition_kind = ""
        self.definition_bracket = False
        # Open hidden or verbatim environment: (name, file:line).
        self.hidden_env: tuple[str, str] | None = None
        self.opaque_env: tuple[str, str] | None = None
        self.includeonly: set[str] | None = None
        self.open_files: list[Path] = []
        self.files: list[Path] = []
        self.lines: list[Line] = []
        self.warnings: list[str] = []
        self.warned: set[str] = set()

    def visible(self) -> bool:
        return self.hidden_env is None and all(f.visible for f in self.stack)

    def in_definition(self) -> bool:
        return (self.definition_groups > 0
                and self.depth > self.definition_depth)

    def warn_once(self, key: str, message: str):
        if key not in self.warned:
            self.warned.add(key)
            self.warnings.append(message)

    def read(self, path: Path, shown: str, bases: list[Path],
             body_only: bool = False):
        real = path.resolve()
        if real in self.open_files:
            self.warnings.append(f"{shown}: skipped, it includes itself")
            return
        try:
            data = path.read_bytes()
        except OSError as e:
            self.warnings.append(f"{shown}: cannot read ({e.strerror})")
            return
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
            self.warnings.append(f"{shown}: not valid UTF-8; read as Latin-1")
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if lines[-1] == "":
            lines.pop()
        numbers = list(range(1, len(lines) + 1))
        if body_only or (self.open_files and _is_document(lines)):
            # A \subfile, or a document of its own read with \input (as with
            # the standalone or docmute packages): only its body is used.
            lines, numbers = _document_body(lines, numbers)
        self.open_files.append(real)
        if real not in self.files:
            self.files.append(real)
        depth = self.depth
        for n, line in zip(numbers, lines):
            self._line(line, shown, n, path.parent, bases)
        self._end_file(shown, depth)
        self.open_files.pop()

    def _end_file(self, shown: str, depth: int):
        """Close what a file leaves open, as LaTeX does at the end of an
        \\input file."""
        if len(self.open_files) > 1 and not all(f.visible for f in self.stack):
            # LaTeX was skipping text, looking for a \fi: it adds one.
            frame = self.stack.pop()
            self.warnings.append(
                f"{frame.where}: {frame.name} has no \\fi in {shown}; like "
                f"LaTeX, texlens ends it at the end of the file")
        if self.hidden_env is not None:
            name, where = self.hidden_env
            end = f"\\end{{{name}}}"
            if name in self.line_envs:
                end = "line with just " + end
            self.warnings.append(
                f"{where}: \\begin{{{name}}} has no {end} in {shown}; "
                f"texlens hides the rest of the file")
        if self.opaque_env is not None:
            name, where = self.opaque_env
            self.warnings.append(
                f"{where}: \\begin{{{name}}} has no \\end{{{name}}} in "
                f"{shown}")
        if self.depth > depth:
            self.warnings.append(
                f"{shown}: {self.depth - depth} unclosed brace(s) at the end "
                f"of the file")
        self.depth = depth
        self.definition_groups = 0
        self.hidden_env = self.opaque_env = None

    def _emit(self, text: str, shown: str, n: int, opaque: bool = False):
        if text.strip():
            self.lines.append(Line(text, shown, n, opaque))

    def _line(self, text: str, shown: str, n: int, here: Path,
              bases: list[Path]):
        if self.hidden_env is not None:
            end = "\\end{" + self.hidden_env[0] + "}"
            if self.hidden_env[0] in self.line_envs:
                if text.rstrip(" \t") == end:
                    self.hidden_env = None
            elif end in text:  # the rest of the line is dropped too
                self.hidden_env = None
            return
        if not text.strip():
            if self.visible():
                # Blank lines separate paragraphs, which keeps TeXcount fast.
                self.lines.append(Line("", shown, n,
                                       self.opaque_env is not None))
            return
        while text:
            if self.opaque_env is not None:
                text = self._opaque(text, shown, n)
            else:
                text = self._code(text, shown, n, here, bases)

    def _opaque(self, text: str, shown: str, n: int) -> str:
        """Keep verbatim text up to the end of its environment. Returns the
        rest of the line, from \\end{...} on."""
        name = self.opaque_env[0]
        m = re.search(r"\\end\s*\{" + re.escape(name) + r"\}", text)
        if m is None:
            self.lines.append(Line(text, shown, n, opaque=True))
            return ""
        self.opaque_env = None
        self._emit(text[:m.start()], shown, n, opaque=True)
        return text[m.start():]

    def _gap(self, gap: str):
        """Text between tokens. Before the brace groups of a definition, only
        optional arguments [...] may come: anything else means that it was
        not a definition after all (except for \\def, whose parameter text
        can be anything)."""
        if (not self.definition_groups or self.definition_kind == "def"
                or self.depth != self.definition_depth):
            return
        for c in gap:
            if c == "[":
                self.definition_bracket = True
            elif c == "]":
                self.definition_bracket = False
            elif not (self.definition_bracket or c.isspace()):
                self.definition_groups = 0
                return

    def _frame(self, m, rest: str, where: str) -> tuple[str | None, int]:
        """The kind of frame that an \\if... token opens (None if the token
        is not a conditional ended by \\fi), and how many characters of
        `rest` the test uses."""
        name = m.group("ifname")
        if name == "":
            return _compare_characters(rest)
        if name in ("false", "true"):
            return name, 0
        following = rest.lstrip()[:1]
        if name in self.false_ifs or name in self.true_ifs:
            if (name not in self.switches and following == "{"
                    and _two_groups(rest)):
                # e.g. the ifdraft package's \ifdraft{draft text}{final text}
                self.warn_once(
                    "args:" + name,
                    f"{where}: \\if{name} takes arguments here, so texlens "
                    f"cannot evaluate it as a conditional and keeps both "
                    f"texts")
                return None, 0
            return ("false" if name in self.false_ifs else "true"), 0
        if name in self.switches:
            return ("true" if self.switches[name] else "false"), 0
        if name in NOT_CONDITIONALS or following in ("{", "["):
            return None, 0  # most likely a macro that takes arguments
        return "other", 0

    def _code(self, text: str, shown: str, n: int, here: Path,
              bases: list[Path]) -> str:
        """Process LaTeX code. Returns the rest of the line if a verbatim
        environment starts in it."""
        code, comment = split_comment(text)
        kept, pos = [], 0
        for m in _TOKEN.finditer(code):
            visible = self.visible()
            if visible:
                gap = code[pos:m.start()]
                kept.append(gap)
                self._gap(gap)
            pos = m.end()
            kind = m.lastgroup
            keep_token = visible
            defining = self.in_definition()
            if kind == "if" and not defining:
                where = f"{shown}:{n}"
                frame, used = self._frame(m, code[m.end():], where)
                if frame is not None:
                    if m.group("unless") and frame != "other":
                        frame = "true" if frame == "false" else "false"
                    self.stack.append(_Frame(
                        frame, frame != "false",
                        re.sub(r"\s+", "", m.group(0)), where))
                    keep_token = visible and frame == "other"
                    pos += used  # the characters that \if compares
            elif kind == "else" and self.stack and not defining:
                top = self.stack[-1]
                if top.kind != "other":
                    top.visible = not top.visible
                    keep_token = False
            elif kind == "fi" and self.stack and not defining:
                top = self.stack.pop()
                keep_token = visible and top.kind == "other"
            elif not visible:
                continue
            elif kind in _DEFINITION_GROUPS and not defining:
                self.definition_groups = _DEFINITION_GROUPS[kind]
                self.definition_depth = self.depth
                self.definition_kind = kind
                self.definition_bracket = False
            elif kind == "let" and self.depth == 0:
                name, target = m.group("letname"), m.group("lettarget")
                if name.startswith("if") and target in ("iftrue", "iffalse"):
                    self.switches[name[2:]] = target == "iftrue"
            elif kind == "newif" and not defining:
                self.switches[m.group("newname")] = False
            elif kind == "set" and self.depth == 0:
                name = m.group("setname")
                if name in self.switches:
                    self.switches[name] = m.group("setval") == "true"
            elif kind == "begin" and not defining:
                env = m.group("benv").strip()
                if env in self.hidden_envs:
                    # The rest of the line is dropped, even after an
                    # \end{env}, which ends the environment unless it comes
                    # from the comment package.
                    self._emit("".join(kept), shown, n)
                    if (env in self.line_envs
                            or "\\end{" + env + "}" not in text[m.end():]):
                        self.hidden_env = (env, f"{shown}:{n}")
                    return ""
                if env in OPAQUE_ENVIRONMENTS:
                    self._emit("".join(kept) + m.group(0), shown, n)
                    self.opaque_env = (env, f"{shown}:{n}")
                    return text[m.end():]
            elif kind in ("input", "inputbare", "import") and not defining:
                self._emit("".join(kept), shown, n)
                kept = []
                self._include(m, shown, n, here, bases)
                continue
            elif kind == "includeonly" and not defining:
                self.includeonly = {s.strip()
                                    for s in m.group("only").split(",")}
            elif kind == "package" and not defining:
                for package in m.group("packages").split(","):
                    if package.strip() == "comment":
                        self.line_envs.add("comment")
                    elif package.strip() == "verbatim":
                        self.line_envs.discard("comment")
            elif kind == "excludecomment" and not defining:
                env = m.group("excluded").strip()
                self.hidden_envs.add(env)
                self.line_envs.add(env)
            elif kind == "lbrace":
                self.depth += 1
            elif kind == "rbrace":
                if (self.definition_groups and not self.definition_bracket
                        and self.depth == self.definition_depth + 1):
                    self.definition_groups -= 1
                self.depth = max(self.depth - 1, 0)
            if keep_token:
                kept.append(m.group(0))
        if self.visible():
            rest = code[pos:]
            self._gap(rest)
            kept.append(rest + comment)
        self._emit("".join(kept), shown, n)
        return ""

    def _include(self, m, shown: str, n: int, here: Path, bases: list[Path]):
        kind = m.lastgroup
        body_only = tex_only = False
        if kind == "import":
            directory = m.group("idir").strip()
            name = m.group("ifile").strip()
            base = (here if m.group("isub") else self.root) / directory
            candidates, new_bases = [base], [base]
        else:
            name = (m.group("ipath") if kind == "input"
                    else m.group("ibare")).strip()
            command = m.group("icmd") if kind == "input" else "input"
            if (command == "include" and self.includeonly is not None
                    and name not in self.includeonly):
                return
            body_only = command == "subfile"
            tex_only = command == "include"
            candidates = bases + [self.root, here]
            new_bases = bases
        if len(self.open_files) >= MAX_INCLUDE_DEPTH:
            self.warnings.append(f"{shown}:{n}: more than {MAX_INCLUDE_DEPTH} "
                                 f"nested files; skipping {name!r}")
            return
        path = _resolve(name, candidates, tex_only)
        if path is None:
            self.warnings.append(f"{shown}:{n}: cannot find {name!r}")
            return
        self.read(path, _display(path, self.root), new_bases, body_only)


def _compare_characters(rest: str) -> tuple[str, int]:
    """Evaluate \\if followed by two characters, as in \\if 0 or \\if 11.
    Returns the kind of frame and how many characters of `rest` the test
    uses. As in TeX, spaces right after \\if are skipped, and the end of the
    line counts as a space."""
    stripped = rest.lstrip(" \t")
    skipped = len(rest) - len(stripped)
    if not stripped or stripped[0] in "\\{}~":
        return "other", 0
    second = stripped[1] if len(stripped) > 1 else " "
    if second in "\\{}~":
        return "other", 0
    if second == "\t":
        second = " "
    used = skipped + min(len(stripped), 2)
    return ("true" if stripped[0] == second else "false"), used


def group_end(text: str, i: int) -> int:
    """Index after the brace group that starts at text[i], or -1."""
    depth, j = 0, i
    while j < len(text):
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return -1


def _two_groups(rest: str) -> bool:
    """Whether `rest` starts with two brace groups on this line."""
    start = len(rest) - len(rest.lstrip())
    end = group_end(rest, start)
    return end > 0 and rest[end:].lstrip().startswith("{")


def _is_document(lines: list[str]) -> bool:
    """Whether a file starts like a document of its own, with
    \\documentclass."""
    for text in lines:
        code = split_comment(text)[0].strip()
        if not code:
            continue
        if _DOCUMENTCLASS.search(code):
            return True
        if not _BEFORE_CLASS.match(code):
            return False
    return False


def _document_body(lines: list[str], numbers: list[int]):
    """The part of a file between \\begin{document} and \\end{document}."""
    begin = next((i for i, text in enumerate(lines)
                  if BEGIN_DOCUMENT.search(split_comment(text)[0])), None)
    if begin is None:
        return lines, numbers
    body = lines[begin:]
    body[0] = BEGIN_DOCUMENT.split(body[0], 1)[1]
    for i, text in enumerate(body):
        if END_DOCUMENT.search(split_comment(text)[0]):
            body[i] = END_DOCUMENT.split(text, 1)[0]
            del body[i + 1:]
            break
    return body, numbers[begin:begin + len(body)]


def _resolve(name: str, directories: list[Path],
             tex_only: bool = False) -> Path | None:
    """The file that \\input{name} reads: like TeX, try name.tex first
    (\\include reads only name.tex)."""
    for directory in directories:
        candidates = [directory / (name + ".tex")]
        if not tex_only:
            candidates.append(directory / name)
        for candidate in candidates:
            if candidate.is_file():
                return candidate
    return None


def _display(path: Path, root: Path) -> str:
    """How messages show a file: relative to the main file's directory."""
    try:
        return Path(os.path.relpath(path, root)).as_posix()
    except ValueError:  # on another drive (Windows)
        return str(path)


def load(main, false_ifs=(), true_ifs=(), hidden_envs=("comment",)
         ) -> Document:
    """Read `main` and everything it includes, as LaTeX would.

    `false_ifs` and `true_ifs` name conditionals to evaluate (e.g. "iffinal").
    """
    main = Path(main).absolute()
    reader = _Reader(main.parent, false_ifs, true_ifs, hidden_envs)
    reader.read(main, main.name, [])
    for frame in reader.stack:
        state = "keeps" if frame.visible else "hides"
        reader.warnings.append(
            f"{frame.where}: {frame.name} is never closed with \\fi; texlens "
            f"{state} the text after it")
    return Document(main, reader.lines, reader.warnings, reader.files)


def find_main(path: str | None = None) -> Path:
    """The main file: `path` itself, or main.tex, or the only .tex file in the
    directory that contains \\documentclass."""
    target = Path(path) if path else Path.cwd()
    if target.is_file():
        return target
    if not target.is_dir():
        raise FileNotFoundError(f"{path}: no such file or directory")
    if (target / "main.tex").is_file():
        return target / "main.tex"
    candidates = []
    for tex in sorted(target.glob("*.tex")):
        if not tex.is_file():
            continue
        text = tex.read_text(encoding="utf-8", errors="replace")
        if any(_DOCUMENTCLASS.search(split_comment(line)[0])
               for line in text.split("\n")):
            candidates.append(tex)
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise FileNotFoundError(f"no main .tex file in {target}")
    names = ", ".join(c.name for c in candidates)
    raise FileNotFoundError(
        f"several main files in {target} ({names}); pass the one to use")
