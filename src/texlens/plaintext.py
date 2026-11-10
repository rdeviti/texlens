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
    "label": 1, "index": 1, "glossary": 1, "vspace": 1, "addvspace": 1,
    "includegraphics": 1, "begin": 1, "end": 1, "color": 1, "pagecolor": 1,
    "phantom": 1, "hphantom": 1, "vphantom": 1, "footnotemark": 0,
    "todo": 1, "nocite": 1, "bibliography": 1, "bibliographystyle": 1,
    "pagebreak": 0, "newpage": 0, "clearpage": 0, "enlargethispage": 1,
    "hyphenation": 1, "setlength": 2, "addtolength": 2, "setcounter": 2,
}
# Dropped too, with a warning, because the text around them may need edits.
CITATIONS = {
    "cite": 1, "citep": 1, "citet": 1, "citealp": 1, "citealt": 1,
    "citeauthor": 1, "citeyear": 1, "citenum": 1, "parencite": 1,
    "textcite": 1, "autocite": 1, "footcite": 1,
}
FOOTNOTES = {"footnote": 1, "footnotetext": 1, "thanks": 1}
REFERENCES = {name: n for name, n in BUILTIN_IGNORE.items()
              if name not in ("ccsdesc", "received", "keywords")}
REFERENCES.update({"ref": 1, "pageref": 1, "vref": 1, "Vref": 1,
                   "vpageref": 1})

# Macros that print nothing.
SILENT = {
    "xspace", "noindent", "indent", "smallskip", "medskip", "bigskip",
    "nobreak", "relax", "ignorespaces", "unskip", "protect", "centering",
    "raggedright", "raggedleft", "item", "maketitle", "else", "fi",
    "normalfont", "rmfamily", "sffamily", "upshape", "mdseries", "tiny",
    "scriptsize", "footnotesize", "small", "normalsize", "large", "Large",
    "LARGE", "huge", "Huge", "selectfont", "leavevmode", "allowbreak",
    "frenchspacing", "nonfrenchspacing", "sloppy", "fussy",
}
# Macros that print a space.
SPACES = {"newline", "linebreak", "quad", "qquad", "enspace", "thinspace",
          "space", "nobreakspace", "hfill", "hspace"}
SYMBOLS = {
    "LaTeX": "LaTeX", "TeX": "TeX", "LaTeXe": "LaTeX2e", "ldots": "...",
    "dots": "...", "textellipsis": "...", "textendash": "–",
    "textemdash": "—", "textquoteleft": "‘", "textquoteright": "’",
    "textquotedblleft": "“", "textquotedblright": "”", "textbackslash": "\\",
    "textasciitilde": "~", "textasciicircum": "^", "textunderscore": "_",
    "textbar": "|", "textless": "<", "textgreater": ">", "textbullet": "•",
    "textperiodcentered": "·", "textdegree": "°", "degree": "°",
    "texttimes": "×", "textpm": "±", "textmu": "µ", "textonehalf": "½",
    "textregistered": "®", "texttrademark": "™", "copyright": "©",
    "textcopyright": "©", "textsection": "§", "S": "§", "P": "¶",
    "euro": "€", "texteuro": "€", "pounds": "£", "textdollar": "$",
    "ss": "ß", "o": "ø", "O": "Ø", "aa": "å", "AA": "Å", "ae": "æ",
    "AE": "Æ", "oe": "œ", "OE": "Œ", "l": "ł", "L": "Ł", "i": "ı", "j": "ȷ",
    "etal": "et al.", "ie": "i.e.,", "eg": "e.g.,",
}
# Formatting that a form may accept: texlens drops it and lists it in a
# warning. Name -> number of arguments (the last one is the text).
EMPHASIS = {
    "emph": 1, "textit": 1, "textbf": 1, "textsl": 1, "texttt": 1,
    "textsc": 1, "underline": 1, "uline": 1, "textsuperscript": 1,
    "textsubscript": 1,
}
# The same as switches, as in {\em text} or {\bfseries text}.
SWITCHES = {"em", "it", "bf", "sl", "tt", "sc", "itshape", "bfseries",
            "slshape", "ttfamily", "scshape"}
# Other macros that print the text of their last argument.
TEXT = {
    "textrm": 1, "textsf": 1, "textup": 1, "textmd": 1, "textnormal": 1,
    "mbox": 1, "hbox": 1, "makebox": 1, "fbox": 1, "framebox": 1, "text": 1,
    "textcolor": 2, "colorbox": 2, "fcolorbox": 3, "href": 2, "parbox": 2,
    "raisebox": 2, "foreignlanguage": 2, "hyperlink": 2, "hypertarget": 2,
    "NoCaseChange": 1,
}
# Conditionals that take their texts as arguments: name -> (arguments, the
# one that texlens keeps).
ARGUMENT_IFS = {"ifthenelse": (3, 1), "iftoggle": (3, 1), "ifbool": (3, 1),
                "ifdef": (3, 1), "ifundef": (3, 1), "IfFileExists": (3, 1)}
VERBATIM = {"url", "path", "nolinkurl"}
ACCENTS = {
    "'": "\u0301", "`": "\u0300", "^": "\u0302", '"': "\u0308",
    "~": "\u0303", "=": "\u0304", ".": "\u0307", "u": "\u0306",
    "v": "\u030c", "H": "\u030b", "c": "\u0327", "k": "\u0328",
    "r": "\u030a", "d": "\u0323", "b": "\u0331",
}
ESCAPES = {"%": "%", "&": "&", "$": "$", "#": "#", "_": "_", "{": "{",
           "}": "}", " ": " ", ",": " ", ";": " ", ":": " ", "!": "",
           "@": "", "/": "", "-": "", ">": " ", "*": ""}

# Math: commands that texlens can write as text.
MATH_SYMBOLS = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "iota": "ι",
    "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π",
    "rho": "ρ", "sigma": "σ", "tau": "τ", "phi": "φ", "varphi": "φ",
    "chi": "χ", "psi": "ψ", "omega": "ω", "Gamma": "Γ", "Delta": "Δ",
    "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ",
    "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω", "times": "×", "cdot": "·",
    "pm": "±", "mp": "∓", "approx": "≈", "sim": "~", "simeq": "≃",
    "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "ne": "≠", "neq": "≠",
    "ll": "≪", "gg": "≫", "to": "→", "rightarrow": "→", "leftarrow": "←",
    "Rightarrow": "⇒", "leftrightarrow": "↔", "infty": "∞", "ldots": "...",
    "cdots": "⋯", "dots": "...", "in": "∈", "subset": "⊂", "cup": "∪",
    "cap": "∩", "forall": "∀", "exists": "∃", "propto": "∝", "circ": "∘",
    "log": "log", "ln": "ln", "exp": "exp", "min": "min", "max": "max",
    "%": "%", "$": "$", "&": "&", "#": "#", "_": "_", "{": "{", "}": "}",
    ",": " ", ";": " ", ":": " ", "!": "", " ": " ", "|": "‖",
}
_MATH_TEXT = re.compile(r"\\(?:mathrm|mathit|mathbf|mathsf|mathtt|mathnormal"
                        r"|text|textrm|textit|textbf|mbox|operatorname)\s*"
                        r"\{([^{}\\$]*)\}")
SUPERSCRIPTS = dict(zip("0123456789+-=()ni", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ"))
SUBSCRIPTS = dict(zip("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎"))

_CONTROL_WORD = re.compile(r"\\([A-Za-z@]+)(\*?)")
_UNEVALUATED_IFS = {"if", "ifx", "ifnum", "ifdim", "ifcat", "ifodd",
                    "ifcase", "ifmmode", "ifvmode", "ifhmode", "ifinner",
                    "ifdefined", "ifcsname"}
_PARAGRAPH = "\x00"  # marks \par in converted text
_EXPANSIONS = 10000  # at most this many macro expansions


@dataclass
class _Context:
    dropped: dict[str, int]
    changes: set[str]
    macros: dict           # name -> Definition, for expansion
    switches: dict[str, bool] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    warned: set[str] = field(default_factory=set)
    formatting: list[str] = field(default_factory=list)
    expansions: int = _EXPANSIONS

    def warn(self, key: str, message: str):
        if key not in self.warned:
            self.warned.add(key)
            self.warnings.append(message)

    def formatted(self, source: str):
        """Remember formatting that was dropped, to list it at the end."""
        if source not in self.formatting:
            self.formatting.append(source)


def _skip_spaces(text: str, i: int) -> int:
    while i < len(text) and text[i] in " \t\n":
        i += 1
    return i


def _token(text: str, i: int):
    """An argument given without braces, as in \\sys 5: the next character
    or control word. Returns (argument, position after it) or None."""
    i = _skip_spaces(text, i)
    if i >= len(text) or text[i] in "}%":
        return None
    m = _CONTROL_WORD.match(text, i)
    if m:
        return m.group(0), m.end()
    if text[i] == "\\":
        return text[i:i + 2], i + 2
    return text[i], i + 1


def _arguments(text: str, i: int, count: int, optional: int = 0,
               tokens: bool = False):
    """Read up to `optional` [...] arguments and `count` {...} arguments.
    Returns (optional ones, mandatory ones, position after them); a missing
    mandatory argument ends the reading, unless `tokens` allows an argument
    without braces."""
    options, args = [], []
    while len(options) < optional or optional < 0:
        group = read_group(text, i, "[")
        if group is None:
            break
        options.append(group[0])
        i = group[1]
    for _ in range(count):
        group = read_group(text, i, "{")
        if group is None and tokens:
            group = _token(text, i)
        if group is None:
            break
        args.append(group[0])
        i = group[1]
    return options, args, i


def _short(text: str, limit: int = 30) -> str:
    text = re.sub(r"\s+", " ", text.replace(_PARAGRAPH, " ")).strip()
    return text if len(text) <= limit else text[:limit - 3].rstrip() + "..."


def convert(text: str, ctx: _Context, depth: int = 0) -> str:
    """Plain text of a piece of LaTeX (without comments)."""
    if depth > 50:
        ctx.warn("depth", "macros nest too deeply; the text is incomplete")
        return ""
    out, i = [], 0
    while i < len(text):
        c = text[i]
        if c == "\\":
            m = _CONTROL_WORD.match(text, i)
            if m is None:  # a control symbol, such as \% or \'
                i = _control_symbol(text, i, out, ctx, depth)
            elif m.group(1) in SWITCHES and m.group(1) not in ctx.macros:
                # {\em text}: the switch lasts until the end of the group.
                rest = convert(text[m.end():], ctx, depth + 1)
                out.append(rest)
                ctx.formatted(f"{{\\{m.group(1)} {_short(rest)}}}")
                i = len(text)
            else:
                i = _macro(text, m, out, ctx, depth)
        elif c == "{":
            group = read_group(text, i, "{")
            if group is None:
                i += 1
                continue
            out.append(convert(group[0], ctx, depth + 1))
            i = group[1]
        elif c == "}":
            i += 1
        elif c == "$":
            i = _dollar_math(text, i, out, ctx)
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
        elif c == "'":
            out.append("’")
            i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _control_symbol(text, i, out, ctx, depth) -> int:
    symbol = text[i + 1:i + 2]
    if symbol == "\\":  # a line break, as in \\ or \\*[2pt]
        i += 2
        if text.startswith("*", i):
            i += 1
        group = read_group(text, i, "[")
        out.append(" ")
        return group[1] if group else i
    if symbol in "([":  # \( ... \) or \[ ... \]
        close = "\\)" if symbol == "(" else "\\]"
        end = text.find(close, i + 2)
        if end < 0:
            return i + 2
        out.append(_math(text[i + 2:end], text[i:end + 2], ctx))
        return end + 2
    if symbol in ACCENTS and symbol not in ESCAPES:
        return _accent(text, i + 2, symbol, out, ctx, depth)
    out.append(ESCAPES.get(symbol, symbol))
    return i + 2


def _accent(text, i, symbol, out, ctx, depth) -> int:
    """\\'e or \\'{e}: the letter with its accent."""
    i = _skip_spaces(text, i)
    group = read_group(text, i, "{")
    if group is not None:
        letter, i = convert(group[0], ctx, depth + 1), group[1]
    else:
        token = _token(text, i)
        if token is None:
            return i
        letter, i = convert(token[0], ctx, depth + 1), token[1]
    out.append(unicodedata.normalize("NFC", letter + ACCENTS[symbol]))
    return i


def _dollar_math(text, i, out, ctx) -> int:
    delimiter = "$$" if text.startswith("$$", i) else "$"
    start = i + len(delimiter)
    end = text.find(delimiter, start)
    if end < 0:
        out.append("$")
        return i + 1
    out.append(_math(text[start:end], text[i:end + len(delimiter)], ctx))
    return end + len(delimiter)


def _scripts(m) -> str | None:
    table = SUPERSCRIPTS if m.group(1) == "^" else SUBSCRIPTS
    content = m.group(2) if m.group(2) is not None else m.group(3)
    content = content.replace(" ", "")
    if content and all(ch in table for ch in content):
        return "".join(table[ch] for ch in content)
    return m.group(0)


def _math(content: str, source: str, ctx: _Context) -> str:
    """Math as text, as in O(n²) for $O(n^2)$, if texlens can write it;
    else the LaTeX source, with a warning."""
    text = re.sub(r"\^\s*(?:\\circ|\{\s*\\circ\s*\})", "°", content)
    text = _MATH_TEXT.sub(r"\1", text)
    text = re.sub(r"(?<!\\)([_^])\s*(?:\{([^{}]*)\}|([^\s{}\\]))", _scripts,
                  text)
    unknown = re.search(r"(?<!\\)[_^]", text)
    text = re.sub(r"(?<!\\)[{}]", "", text)  # braces that only group
    text = re.sub(r"\\([A-Za-z]+|.)",
                  lambda m: MATH_SYMBOLS.get(m.group(1), m.group(0)), text)
    if unknown or "\\" in text:
        ctx.warn("math", "math that texlens cannot write as text is kept as "
                 f"LaTeX, as in {source}")
        return source
    # Italic letters and exponents are formatting; symbols such as $\times$
    # are not.
    letters = re.sub(r"\\[A-Za-z]+", "", _MATH_TEXT.sub("", content))
    if re.search(r"[A-Za-z]", letters) or re.search(r"[_^](?!\s*\{?\s*"
                                                     r"\\circ)", content):
        ctx.formatted(source)
    return text


def _conditional(text, m, out, ctx, depth) -> int:
    """A conditional that texlens did not evaluate, such as one in a macro
    definition: keep the text before its \\else."""
    name, i = m.group(1), m.end()
    if name in ARGUMENT_IFS:
        count, keep = ARGUMENT_IFS[name]
        _, args, k = _arguments(text, i, count)
        ctx.warn(name, f"\\{name}: texlens cannot evaluate this conditional; "
                 "kept the text of its first branch")
        if len(args) == count:
            out.append(convert(args[keep], ctx, depth + 1))
        return k
    level, branch_end, k = 1, None, i
    for word in re.finditer(r"\\([A-Za-z@]+)", text[i:]):
        w = word.group(1)
        if w.startswith("if") and w not in ctx.macros \
                and w not in ARGUMENT_IFS:
            level += 1
        elif w == "else" and level == 1 and branch_end is None:
            branch_end = i + word.start()
        elif w == "fi":
            level -= 1
            if level == 0:
                k = i + word.end()
                break
    else:  # no \fi: not a conditional, or one that ends elsewhere
        ctx.warn(name, f"\\{name}: unknown macro or a conditional without "
                 "\\fi; left out")
        return i
    end = branch_end if branch_end is not None else k - 3
    if name in ctx.switches:  # as in \newif\ifdraft \drafttrue
        if ctx.switches[name]:
            out.append(convert(text[i:end], ctx, depth + 1))
        elif branch_end is not None:
            out.append(convert(text[branch_end + 5:k - 3], ctx, depth + 1))
        return k
    if name in _UNEVALUATED_IFS:
        ctx.warn(name, f"\\{name}: texlens cannot evaluate this conditional; "
                 "its text is left out")
        return k
    ctx.warn(name, f"\\{name}: texlens cannot evaluate this conditional "
             "here; kept the text before its \\else")
    out.append(convert(text[i:end], ctx, depth + 1))
    return k


def _expand(text, m, out, ctx, depth) -> int:
    """A macro that the paper defines: its body, with the arguments."""
    name, i = m.group(1), m.end()
    d = ctx.macros[name]
    if d.delimited:
        out.append(m.group(0))
        ctx.warn(name, f"\\{name}: its parameters are delimited, as in "
                 "\\def\\x#1.{...}; kept as it is")
        return i
    ctx.expansions -= 1
    if ctx.expansions < 0:
        ctx.warn("expansions", "too many macro expansions (a macro that "
                 "calls itself?); the text is incomplete")
        return len(text)
    options, args, i = _arguments(text, i, d.args, d.optional, tokens=True)
    if not d.args and text.startswith("{}", i):
        i += 2  # as in \sys{}
    params = (options + [d.default] * d.optional)[:d.optional] + args

    def parameter(p):
        n = int(p.group(2))
        value = params[n - 1] if n <= len(params) else ""
        # Keep \else#1 apart, as TeX does, instead of making it \elsetext.
        return (p.group(1) + " " if p.group(1) else "") + value
    body = re.sub(r"(\\[A-Za-z@]+)?#(\d)", parameter, d.body)
    out.append(convert(body, ctx, depth + 1))
    return i


def _macro(text, m, out, ctx, depth) -> int:
    name, i = m.group(1), m.end()
    if name in REFERENCES:
        ctx.warn(name, f"\\{name}: dropped, but LaTeX prints a reference "
                 "there; edit the text around it")
    elif name in CITATIONS:
        ctx.warn(name, f"\\{name}: dropped the citation; check the text "
                 "around it")
    elif name in FOOTNOTES:
        ctx.warn(name, f"\\{name}: dropped, with its text")
    if name in ctx.dropped:
        return _arguments(text, i, ctx.dropped[name], optional=-1)[2]
    if name in ctx.changes:
        _, args, i = _arguments(text, i, 1, optional=-1)
        out.append(convert(args[0], ctx, depth + 1) if args else "")
        return i
    if name in ctx.macros:
        return _expand(text, m, out, ctx, depth)
    if name.startswith("if") or name in ARGUMENT_IFS:
        return _conditional(text, m, out, ctx, depth)
    if name in SYMBOLS:
        out.append(SYMBOLS[name])
        return i + 2 if text.startswith("{}", i) else i
    if name == "par":
        out.append(_PARAGRAPH)
        return i
    if name in SILENT:
        return i
    if name in SPACES:
        out.append(" ")
        return _arguments(text, i, 1 if name == "hspace" else 0)[2]
    if name in ACCENTS:  # \c{c}, \v{c}, ...
        return _accent(text, i, name, out, ctx, depth)
    if name in VERBATIM:
        _, args, i = _arguments(text, i, 1)
        out.append(args[0] if args else "")
        return i
    if name in ("verb", "lstinline") and i < len(text) \
            and text[i] not in "{[":
        end = text.find(text[i], i + 1)
        if end > 0:
            out.append(text[i + 1:end])
            return end + 1
    if name == "enquote":
        _, args, i = _arguments(text, i, 1)
        out.append("“" + convert(args[0], ctx, depth + 1) + "”"
                   if args else "")
        return i
    if name == "ensuremath":
        _, args, i = _arguments(text, i, 1)
        if args:
            out.append(_math(args[0], text[m.start():i], ctx))
        return i
    if name in EMPHASIS or name in TEXT:
        _, args, i = _arguments(text, i, EMPHASIS.get(name) or TEXT[name],
                                optional=-1)
        content = convert(args[-1], ctx, depth + 1) if args else ""
        out.append(content)
        if name in EMPHASIS and content.strip():
            ctx.formatted(f"\\{name}{{{_short(content)}}}")
        return i
    j = _skip_spaces(text, i)
    if j < len(text) and text[j] in "{[":
        _, args, k = _arguments(text, j, 1, optional=-1)
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
    # Spaces left by a dropped \cite, but not the one before an ellipsis.
    text = re.sub(r" +(?!\.\.\.)([.,;:!?)\]])", r"\1", text)
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


def _find(code: CodeText):
    """The span of the abstract's text in `code`, or None."""
    masked = code.masked
    m = _ABSTRACT_ENV.search(masked)
    if m:
        return m.span(1)
    m = _ABSTRACT_MACRO.search(masked)
    group = read_group(masked, m.end(), "{", match_braces(masked)) \
        if m else None
    if group is None:
        return None
    end = group[1] - 1
    return end - len(group[0]), end


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
    left out, the paper's macros are expanded, and formatting is dropped.
    Paragraphs are separated by blank lines."""
    code, preamble = doc.body(), doc.preamble()
    span = _find(code)
    if span is None:  # some classes, such as MDPI's, take it in the preamble
        code = preamble
        span = _find(code)
    if span is None:
        raise NoAbstractError("no abstract found: texlens looks for "
                              "\\begin{abstract} ... \\end{abstract} "
                              "or \\abstract{...}")
    start, end = span
    dropped = dict(DROPPED)
    dropped.update(CITATIONS)
    dropped.update(FOOTNOTES)
    dropped.update(REFERENCES)
    for spec in cfg.notes + cfg.ignore:
        name, n = macro_spec(spec)
        dropped[name] = n
    sources = preamble.masked
    if code is not preamble:
        sources += "\n" + code.masked[:start]  # definitions before it
    ctx = _Context(dropped=dropped,
                   changes={macro_spec(s)[0] for s in cfg.changes},
                   macros={d.name: d for d in definitions(sources)
                           if d.body is not None},
                   switches=doc.switches)
    paragraphs = []
    for block in re.split(r"\n[ \t]*\n", _lines(code, start, end)):
        paragraphs += convert(block, ctx).split(_PARAGRAPH)
    text = "\n\n".join(p for p in map(_tidy, paragraphs) if p)
    if ctx.formatting:
        ctx.warnings.append("formatting removed; add it back if the form "
                            "accepts it: " + ", ".join(ctx.formatting))
    file, line = code.where(start)
    return Abstract(text, file, line, ctx.warnings)
