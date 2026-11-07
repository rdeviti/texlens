"""Word count of the abstract and body, with TeXcount doing the counting.

TeXcount does not evaluate conditionals and does not know the paper's own
macros, so we hand it a flattened copy of the compiled text (see source.py)
together with rules for those macros.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import Config, macro_spec
from .source import (OPAQUE_ENVIRONMENTS, Document, Line,
                     empty_inline_verbatim, split_comment)

# Common macros TeXcount 3.1 does not know whose arguments are not counted:
# references, and front matter that word limits leave out, like the title
# (ACM's classification concepts and dates, and keywords).
BUILTIN_IGNORE = {
    "cref": 1, "Cref": 1, "crefrange": 2, "Crefrange": 2, "cpageref": 1,
    "Cpageref": 1, "cpagerefrange": 2, "Cpagerefrange": 2, "labelcref": 1,
    "labelcpageref": 1, "namecref": 1, "nameCref": 1, "lcnamecref": 1,
    "autoref": 1, "Autoref": 1, "nameref": 1, "eqref": 1,
    "ccsdesc": 1, "received": 1, "keywords": 1,
}

# Environments TeXcount 3.1 does not know, and how it should count them:
# not at all (xall) for front matter (ACM's CCSXML, which LaTeX does not
# print, and the keywords of IEEEtran and elsarticle) and verbatim text; as a
# figure (float) for acmart's teaser figure.
BUILTIN_ENVIRONMENTS = {
    "CCSXML": "xall", "IEEEkeywords": "xall", "keyword": "xall",
    "teaserfigure": "float",
    **{env: "xall" for env in OPAQUE_ENVIRONMENTS - {"verbatim"}},
}

# TeXcount instructions in the preamble that are not copied: they do not
# apply to the flattened text, or they change TeXcount's output.
_NOT_COPIED = {"ignore", "endignore", "insert", "break", "template",
               "newtemplate", "log", "assert"}

_DIRECTIVE = re.compile(r"%+tc:\s*(\w+)", re.I)
_PACKAGES = re.compile(r"\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?"
                       r"\s*\{([^{}]*)\}")
_FLOAT_EDGE = re.compile(r"\\(begin|end)\s*\{(?:figure|table|wrapfigure|"
                         r"wraptable|sidewaysfigure|sidewaystable|algorithm)"
                         r"\*?\}")


class TeXcountError(Exception):
    pass


@dataclass
class WordCount:
    text: int
    headers: int
    captions: int
    inline_math: int
    display_math: int
    total: int
    start: str
    stop: str
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def directive(comment: str) -> str | None:
    """The TeXcount instruction that a comment holds, in lower case, such as
    "ignore" for "%TC:ignore"; None if it holds none. As for TeXcount, the
    comment must start with %TC: (with one or more %, in any case)."""
    m = _DIRECTIVE.match(comment)
    return m.group(1).lower() if m else None


def texcount_command(explicit: str | None = None) -> list[str]:
    """The command that runs TeXcount, as a list of arguments."""
    command = explicit or os.environ.get("TEXLENS_TEXCOUNT") or "texcount"
    if shutil.which(command):  # a path, possibly with spaces
        return [command]
    try:
        parts = shlex.split(command, posix=os.name != "nt")
    except ValueError:  # unbalanced quotes
        parts = []
    if os.name == "nt":
        parts = [p[1:-1] if len(p) > 1 and p[0] == p[-1] == '"' else p
                 for p in parts]
    if not parts or shutil.which(parts[0]) is None:
        raise TeXcountError(
            f"{parts[0] if parts else command!r} not found. TeXcount ships "
            "with TeX Live and MacTeX (tlmgr install texcount; on Debian/"
            "Ubuntu: apt install texlive-extra-utils). Use --texcount or "
            "TEXLENS_TEXCOUNT to point to it.")
    return parts


def rules(cfg: Config) -> list[str]:
    """TeXcount instructions for the paper's macros. The rules in
    `cfg.rules` come last, so that they take precedence."""
    out = [f"%TC:envir {env} [] {how}"
           for env, how in sorted(BUILTIN_ENVIRONMENTS.items())]
    ignore = dict(BUILTIN_IGNORE)
    for spec in cfg.notes + cfg.ignore:
        name, n = macro_spec(spec)
        ignore[name] = n
    for name, n in ignore.items():
        out.append(f"%TC:macro \\{name} [{','.join(['ignore'] * n)}]")
    for spec in cfg.changes:
        name, n = macro_spec(spec)
        out.append(f"%TC:macro \\{name} [{','.join(['text'] * n)}]")
    for name, n in cfg.words.items():
        out.append(f"%TC:macroword \\{name} {n}")
    footnotes = "text" if cfg.footnotes else "ignore"
    out.append(f"%TC:macro \\footnote [{footnotes}]")
    out.append(f"%TC:macro \\footnotetext [{footnotes}]")
    for name, rule in cfg.rules.items():
        out.append(f"%TC:macro \\{name} {rule}")
    return out


def preamble_settings(doc: Document) -> list[str]:
    """What TeXcount needs from the preamble: its %TC: instructions, and the
    packages it loads (TeXcount has rules for some, such as xcolor's
    \\textcolor and hyperref's \\href)."""
    out = []
    for line in doc.slice((0, 0), doc.begin_document()):
        comment = "" if line.opaque else split_comment(line.text)[1]
        tag = directive(comment)
        if tag is not None and tag not in _NOT_COPIED:
            out.append(comment.strip())
    packages = []
    for m in _PACKAGES.finditer(doc.preamble().masked):
        for name in m.group(1).split(","):
            name = name.strip()
            if name and name not in packages:
                packages.append(name)
    if packages:
        out.append(r"\usepackage{" + ",".join(packages) + "}")
    return out


def counted_range(doc: Document, cfg: Config):
    """(start, stop, start marker, stop marker) of the counted text."""
    begin = doc.begin_document()
    start = doc.find(cfg.start, begin)
    start_marker = _marker_at(doc, start, cfg.start) if start else None
    if start is None:
        start, start_marker = begin, r"\begin{document}"
        if not doc.has_begin_document():
            start_marker = "start of file"
    stop = doc.find(cfg.stop, start)
    stop_marker = _marker_at(doc, stop, cfg.stop) if stop else None
    if stop is None:
        stop, stop_marker = (len(doc.lines), 0), "end of file"
    return start, stop, start_marker, stop_marker


def _marker_at(doc: Document, pos, markers) -> str:
    code = split_comment(doc.lines[pos[0]].text)[0][pos[1]:]
    for m in sorted(markers, key=len, reverse=True):
        if code.startswith(m):
            return m
    return markers[0]


def ignore_warnings(lines: list[Line]) -> list[str]:
    """%TC:ignore regions that hide text outside figures and tables."""
    warnings, start, buffer = [], None, []
    for line in lines:
        if line.opaque:
            continue
        code, comment = split_comment(line.text)
        tag = directive(comment)
        if start is None:
            if tag == "ignore":
                start, buffer = line, []
            continue
        buffer.append(code)
        if tag == "endignore":
            words = _prose_words("\n".join(buffer))
            if words > 20:
                warnings.append(
                    f"{start.file}:{start.lineno}: %TC:ignore hides about "
                    f"{words} words outside figures and tables")
            start = None
    return warnings


def _prose_words(text: str) -> int:
    """Rough number of words outside figures, tables, and algorithms."""
    parts, depth, pos = [], 0, 0
    for m in _FLOAT_EDGE.finditer(text):
        if m.group(1) == "begin":
            if depth == 0:
                parts.append(text[pos:m.start()])
            depth += 1
        elif depth:
            depth -= 1
            if depth == 0:
                pos = m.end()
    if depth == 0:
        parts.append(text[pos:])
    text = re.sub(r"\\[A-Za-z@]+\*?|[{}$]", " ", " ".join(parts))
    return len(re.findall(r"[A-Za-z]{2,}", text))


def _ends_in_ignore(lines: list[Line]) -> bool:
    """Whether a %TC:ignore region is still open after `lines`."""
    ignoring = False  # TeXcount's ignore regions do not nest
    for line in lines:
        tag = None if line.opaque else directive(split_comment(line.text)[1])
        if tag == "ignore":
            ignoring = True
        elif tag == "endignore":
            ignoring = False
    return ignoring


def flatten(doc: Document, cfg: Config):
    """The text for TeXcount: settings from the preamble, the rules for the
    paper's macros, and the counted range of the compiled text."""
    start, stop, start_marker, stop_marker = counted_range(doc, cfg)
    lines = doc.slice(start, stop)
    body = [line.text if line.opaque else empty_inline_verbatim(line.text)
            for line in lines]
    if _ends_in_ignore(lines):  # e.g. a %TC:ignore just before \bibliography
        body.append("%TC:endignore")
    text = "\n".join(preamble_settings(doc) + rules(cfg)
                     + [r"\begin{document}"] + body
                     + [r"\end{document}", ""])
    return text, lines, start_marker, stop_marker


_FIELDS = {
    "Words in text": "text",
    "Words in headers": "headers",
    "Words outside text (captions, etc.)": "captions",
    "Number of math inlines": "inline_math",
    "Number of math displayed": "display_math",
}


def count_words(doc: Document, cfg: Config, texcount: str | None = None,
                keep: Path | None = None) -> WordCount:
    if keep is not None and Path(keep).resolve() in doc.files:
        raise FileExistsError(f"{keep} is a file of the paper; save the "
                              f"flattened text under another name")
    command = texcount_command(texcount)
    text, lines, start_marker, stop_marker = flatten(doc, cfg)
    if keep is not None:
        Path(keep).write_text(text, encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "texlens-flat.tex"
        path.write_text(text, encoding="utf-8")
        result = subprocess.run(command + ["-utf8", "-total", str(path)],
                                capture_output=True, encoding="utf-8",
                                errors="replace")
    if result.returncode != 0:
        raise TeXcountError(f"TeXcount failed:\n{result.stdout}"
                            f"{result.stderr}")
    out = result.stdout
    counts = {key: 0 for key in _FIELDS.values()}
    for label, value in re.findall(r"^(.+?): (\d+)\s*$", out, re.M):
        if label.strip() in _FIELDS:
            counts[_FIELDS[label.strip()]] = int(value)
    warnings = list(doc.warnings) + ignore_warnings(lines)
    errors = re.search(r"\(errors:(\d+)\)", out)
    if errors:
        details = [line.strip() for line in out.splitlines()
                   if line.startswith("!!!")]
        warnings.append(f"TeXcount reported {errors.group(1)} error(s)"
                        + (": " + "; ".join(details) if details else ""))
    total = counts["text"] + counts["headers"]
    if cfg.captions:
        total += counts["captions"]
    if cfg.math:
        total += counts["inline_math"] + counts["display_math"]
    return WordCount(total=total, start=start_marker, stop=stop_marker,
                     warnings=warnings, **counts)
