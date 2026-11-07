"""Settings: defaults, texlens.toml, and command-line overrides."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_NAME = "texlens.toml"

DEFAULT_START = [r"\begin{abstract}"]
DEFAULT_STOP = [r"\appendix", r"\bibliographystyle", r"\bibliography",
                r"\printbibliography", r"\begin{thebibliography}",
                r"\end{document}"]


class ConfigError(Exception):
    pass


@dataclass
class Config:
    # Macros. Names have no backslash; "name:N" means N mandatory arguments.
    notes: list[str] = field(default_factory=lambda: ["todo"])
    changes: list[str] = field(default_factory=list)
    ignore: list[str] = field(default_factory=list)
    words: dict[str, int] = field(default_factory=dict)
    rules: dict[str, str] = field(default_factory=dict)
    # Conditionals to evaluate, e.g. "ifdraft"; and environments to drop.
    false_ifs: list[str] = field(default_factory=list)
    true_ifs: list[str] = field(default_factory=list)
    hidden_environments: list[str] = field(
        default_factory=lambda: ["comment"])
    # What the word count covers.
    start: list[str] = field(default_factory=lambda: list(DEFAULT_START))
    stop: list[str] = field(default_factory=lambda: list(DEFAULT_STOP))
    footnotes: bool = True
    captions: bool = False
    math: bool = False


def macro_name(name: str, with_count: bool = True) -> str:
    """A macro name without the backslash; with `with_count`, it may end
    in :N, for N mandatory arguments."""
    name = str(name).strip().lstrip("\\")
    pattern = r"[A-Za-z@]+(:\d+)?" if with_count else r"[A-Za-z@]+"
    if not re.fullmatch(pattern, name):
        raise ConfigError(f"invalid macro name: {name!r}")
    return name


def macro_spec(spec: str) -> tuple[str, int]:
    """"name" -> (name, 1); "name:2" -> (name, 2)."""
    name, _, count = spec.partition(":")
    return name, int(count) if count else 1


def conditional_name(name: str) -> str:
    name = str(name).strip().lstrip("\\")
    if not re.fullmatch(r"if[A-Za-z@]+", name):
        raise ConfigError(f"invalid conditional: {name!r} (expected e.g. "
                          "\"ifdraft\")")
    return name


def _toml(path: Path) -> dict:
    try:
        import tomllib
    except ModuleNotFoundError:  # Python < 3.11
        try:
            import tomli as tomllib
        except ModuleNotFoundError:
            raise ConfigError(f"reading {path.name} needs Python 3.11+ or "
                              "the tomli package (pip install tomli)")
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}")


_SCHEMA = {
    "macros": {"notes", "changes", "ignore", "words", "rules"},
    "conditionals": {"false", "true", "hidden_environments"},
    "count": {"start", "stop", "footnotes", "captions", "math"},
}


def _list(value, where: str) -> list[str]:
    if not (isinstance(value, list) and all(isinstance(v, str)
                                            for v in value)):
        raise ConfigError(f"{where} must be a list of strings")
    return value


def _bool(value, where: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{where} must be true or false")
    return value


def load_config(path: Path | None) -> Config:
    """Defaults, updated from a texlens.toml file if `path` is given.

    Macro lists add to the defaults (\\todo is always a note); the other
    lists replace them.
    """
    cfg = Config()
    if path is None:
        return cfg
    data = _toml(path)
    for section, values in data.items():
        if section not in _SCHEMA or not isinstance(values, dict):
            raise ConfigError(f"{path.name}: unknown section [{section}]")
        unknown = set(values) - _SCHEMA[section]
        if unknown:
            raise ConfigError(f"{path.name}: unknown key(s) in [{section}]: "
                              + ", ".join(sorted(unknown)))
    macros = data.get("macros", {})
    for key in ("notes", "changes", "ignore"):
        if key in macros:
            extra = [macro_name(n) for n in _list(macros[key],
                                                  f"macros.{key}")]
            setattr(cfg, key, _merge(getattr(cfg, key), extra))
    if "words" in macros:
        words = macros["words"]
        if not isinstance(words, dict) or not all(
                isinstance(v, int) and not isinstance(v, bool) and v >= 0
                for v in words.values()):
            raise ConfigError("[macros.words] must map names to word counts")
        cfg.words.update({macro_name(k, with_count=False): v
                          for k, v in words.items()})
    if "rules" in macros:
        rules = macros["rules"]
        if not isinstance(rules, dict) or not all(
                isinstance(v, str) for v in rules.values()):
            raise ConfigError("[macros.rules] must map names to TeXcount "
                              "rules such as \"[text,ignore]\"")
        cfg.rules.update({macro_name(k, with_count=False): v
                          for k, v in rules.items()})
    conditionals = data.get("conditionals", {})
    if "false" in conditionals:
        cfg.false_ifs = [conditional_name(n) for n in _list(
            conditionals["false"], "conditionals.false")]
    if "true" in conditionals:
        cfg.true_ifs = [conditional_name(n) for n in _list(
            conditionals["true"], "conditionals.true")]
    if "hidden_environments" in conditionals:
        cfg.hidden_environments = _list(
            conditionals["hidden_environments"],
            "conditionals.hidden_environments")
    count = data.get("count", {})
    for key in ("start", "stop"):
        if key in count:
            setattr(cfg, key, _list(count[key], f"count.{key}"))
    for key in ("footnotes", "captions", "math"):
        if key in count:
            setattr(cfg, key, _bool(count[key], f"count.{key}"))
    return cfg


def _merge(base: list[str], extra: list[str]) -> list[str]:
    """`base` plus `extra`; an item of `extra` replaces an item of `base`
    with the same name, as "todo:2" replaces "todo"."""
    out = list(base)
    for item in extra:
        name = macro_spec(item)[0]
        same = [i for i, old in enumerate(out) if macro_spec(old)[0] == name]
        if same:
            out[same[0]] = item
        else:
            out.append(item)
    return out


def validate(cfg: Config) -> Config:
    """Reject settings that contradict each other."""
    categories: dict[str, list[str]] = {}
    for key in ("notes", "changes", "ignore"):
        for spec in getattr(cfg, key):
            categories.setdefault(macro_spec(spec)[0], []).append(key)
    for name in cfg.rules:
        categories.setdefault(name, []).append("rules")
    for name, keys in categories.items():
        if len(keys) > 1:
            raise ConfigError(
                f"\\{name} is in both {keys[0]} and {keys[1]}; a macro can be "
                f"in only one of notes, changes, ignore, and rules")
    for name in cfg.false_ifs:
        if name in cfg.true_ifs:
            raise ConfigError(f"\\{name} is declared both false and true")
    return cfg


def find_config(main: Path, explicit: str | None) -> Path | None:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise ConfigError(f"{explicit}: no such file")
        return path
    path = main.parent / CONFIG_NAME
    return path if path.is_file() else None


def apply_overrides(cfg: Config, args) -> Config:
    """Command-line options win over the file."""
    for key, option in (("notes", "note"), ("changes", "change"),
                        ("ignore", "ignore")):
        values = getattr(args, option, None) or []
        setattr(cfg, key, _merge(getattr(cfg, key),
                                 [macro_name(v) for v in values]))
    for item in getattr(args, "word", None) or []:
        name, sep, count = item.partition("=")
        if not sep or not count.isdigit():
            raise ConfigError(f"--word {item}: expected NAME=N")
        cfg.words[macro_name(name, with_count=False)] = int(count)
    for item in getattr(args, "false_if", None) or []:
        cfg.false_ifs = _merge(cfg.false_ifs, [conditional_name(item)])
    for item in getattr(args, "true_if", None) or []:
        cfg.true_ifs = _merge(cfg.true_ifs, [conditional_name(item)])
    if getattr(args, "start", None):
        cfg.start = args.start
    if getattr(args, "stop", None):
        cfg.stop = args.stop
    for key in ("footnotes", "captions", "math"):
        value = getattr(args, key, None)
        if value is not None:
            setattr(cfg, key, value)
    return cfg
