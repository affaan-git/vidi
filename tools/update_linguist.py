"""Generate src/vidi/linguist_data.py from a GitHub Linguist release.

    python tools/update_linguist.py v9.7.0

Needs network access and PyYAML (pip install pyyaml). vidi only reads the generated module and
never downloads anything itself. Pass --source DIR to read the YAML files from a folder instead.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.request
import warnings
from pathlib import Path

import yaml

FILES = ("languages.yml", "heuristics.yml", "vendor.yml", "documentation.yml")
RAW = "https://raw.githubusercontent.com/github-linguist/linguist/{tag}/{path}"
OUTPUT = Path(__file__).resolve().parents[1] / "src" / "vidi" / "linguist_data.py"

POSIX_CLASSES = {
    "alpha": "a-zA-Z",
    "digit": "0-9",
    "alnum": "a-zA-Z0-9",
    "upper": "A-Z",
    "lower": "a-z",
    "space": r"\s",
    "word": r"\w",
    "xdigit": "0-9a-fA-F",
    "punct": r"!-/:-@\[-`{-~",
}


def fetch(tag: str, source: Path | None) -> dict[str, str]:
    texts = {}
    for name in (*FILES, "LICENSE"):
        if source is not None:
            texts[name] = (source / name).read_text(encoding="utf-8")
            continue
        path = name if name == "LICENSE" else f"lib/linguist/{name}"
        with urllib.request.urlopen(RAW.format(tag=tag, path=path), timeout=30) as response:
            texts[name] = response.read().decode("utf-8")
    return texts


def ruby_to_python(pattern: str) -> str:
    """A Python form of a Ruby regex as Linguist writes them.

    An inline flag after the start becomes a scoped group running to the end of the enclosing
    group.
    Ruby's (?m) becomes Python's (?s).
    """
    pattern = pattern.replace("[\\s&&[^\\r\\n]]", "[ \\t\\f\\v]")
    out: list[str] = []
    closers = [0]
    i, in_class = 0, False
    while i < len(pattern):
        char = pattern[i]
        if char == "\\" and i + 1 < len(pattern):
            escape = pattern[i + 1]
            if escape == "h":
                out.append("[0-9a-fA-F]" if not in_class else "0-9a-fA-F")
            elif escape == "H":
                out.append("[^0-9a-fA-F]")
            elif escape == "z":
                out.append(r"\Z")
            elif escape == "Z":
                out.append(r"(?=\n?\Z)")
            elif escape == "k" and pattern[i + 2 : i + 3] == "<":
                end = pattern.index(">", i)
                out.append(f"(?P={pattern[i + 3:end]})")
                i = end + 1
                continue
            else:
                out.append(pattern[i : i + 2])
            i += 2
            continue
        if in_class:
            if pattern.startswith("[:", i):
                end = pattern.index(":]", i)
                out.append(POSIX_CLASSES[pattern[i + 2 : end]])
                i = end + 2
                continue
            if char == "]":
                in_class = False
            out.append(char)
            i += 1
            continue
        if char == "[":
            in_class = True
            out.append(char)
            if pattern[i + 1 : i + 2] == "^":
                out.append("^")
                i += 1
            i += 1
            continue
        flags = re.match(r"\(\?([mix]*)(?:-([mix]+))?\)", pattern[i:])
        if flags and (flags.group(1) or flags.group(2)):
            on = flags.group(1).replace("m", "s")
            off = (flags.group(2) or "").replace("m", "s")
            if not out and not off:
                out.append(f"(?{on})")
            else:
                out.append(f"(?{on}{'-' + off if off else ''}:")
                closers[-1] += 1
            i += flags.end()
            continue
        if char == "(":
            if pattern.startswith("(?>", i):
                out.append("(?:")
                i += 3
            else:
                named = re.match(r"\(\?<([A-Za-z_]\w*)>", pattern[i:])
                scoped = re.match(r"\(\?([mix]*)(?:-([mix]+))?:", pattern[i:])
                if named:
                    out.append(f"(?P<{named.group(1)}>")
                    i += named.end()
                elif scoped and (scoped.group(1) or scoped.group(2)):
                    on = scoped.group(1).replace("m", "s")
                    off = (scoped.group(2) or "").replace("m", "s")
                    out.append(f"(?{on}{'-' + off if off else ''}:")
                    i += scoped.end()
                else:
                    out.append("(")
                    i += 1
            closers.append(0)
            continue
        if char == ")":
            out.append(")" * closers.pop() + ")")
            i += 1
            continue
        if char == "+" and out and out[-1] in ("*", "+", "?", "}"):
            i += 1
            continue
        out.append(char)
        i += 1
    out.append(")" * closers[0])
    return "".join(out)


def compiled_or_none(pattern: str, dropped: list[str]) -> str | None:
    converted = ruby_to_python(pattern)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            re.compile(converted, re.MULTILINE)
    except (re.error, FutureWarning) as error:
        dropped.append(f"{pattern!r}: {error}")
        return None
    return converted


def join_patterns(value) -> str:
    patterns = value if isinstance(value, list) else [value]
    return "|".join(f"(?:{p})" for p in patterns) if len(patterns) > 1 else patterns[0]


def condition(rule: dict, named: dict[str, str], dropped: list[str]):
    """A rule's condition as nested tuples, or None when a pattern cannot be converted."""
    if "and" in rule:
        parts = [condition(part, named, dropped) for part in rule["and"]]
        return None if any(part is None for part in parts) else ("all", tuple(parts))
    if "named_pattern" in rule:
        pattern = compiled_or_none(named[rule["named_pattern"]], dropped)
        return None if pattern is None else ("match", pattern)
    if "pattern" in rule:
        pattern = compiled_or_none(join_patterns(rule["pattern"]), dropped)
        return None if pattern is None else ("match", pattern)
    if "negative_pattern" in rule:
        pattern = compiled_or_none(join_patterns(rule["negative_pattern"]), dropped)
        return None if pattern is None else ("absent", pattern)
    return ("always",)


def build(texts: dict[str, str], tag: str) -> tuple[str, list[str]]:
    languages = yaml.safe_load(texts["languages.yml"])
    heuristics = yaml.safe_load(texts["heuristics.yml"])
    dropped: list[str] = []

    table = {
        name: (v["type"], v.get("color"), v.get("group")) for name, v in sorted(languages.items())
    }
    aliases = {}
    for name, v in languages.items():
        for alias in [name, *v.get("aliases", [])]:
            aliases.setdefault(alias.lower(), name)

    extensions: dict[str, list[tuple[bool, str]]] = {}
    for name, v in languages.items():
        for position, extension in enumerate(v.get("extensions", [])):
            extensions.setdefault(extension.lower(), []).append((position != 0, name))
    # Languages that list an extension first come ahead, each group in languages.yml order.
    extensions_out = {
        ext: tuple(name for _, name in sorted(names, key=lambda pair: pair[0]))
        for ext, names in sorted(extensions.items())
    }

    filenames: dict[str, list[str]] = {}
    interpreters: dict[str, list[str]] = {}
    for name, v in languages.items():
        for filename in v.get("filenames", []):
            filenames.setdefault(filename, []).append(name)
        for interpreter in v.get("interpreters", []):
            interpreters.setdefault(interpreter, []).append(name)

    named_patterns = heuristics.get("named_patterns", {})
    named = {key: join_patterns(value) for key, value in named_patterns.items()}
    rules: dict[str, list] = {}
    for disambiguation in heuristics["disambiguations"]:
        converted = []
        for rule in disambiguation["rules"]:
            result = condition(rule, named, dropped)
            if result is None:
                continue
            language = rule["language"]
            names = tuple(language) if isinstance(language, list) else (language,)
            converted.append((names, result))
        for extension in disambiguation.get("extensions", []):
            rules.setdefault(extension.lower(), []).extend(converted)

    def path_patterns(name: str) -> list[str]:
        converted = (compiled_or_none(pattern, dropped) for pattern in yaml.safe_load(texts[name]))
        return [pattern for pattern in converted if pattern]

    vendor = path_patterns("vendor.yml")
    documentation = path_patterns("documentation.yml")

    def mapping(name: str, data: dict) -> list[str]:
        lines = [f"{name} = {{"]
        lines += [f"    {ascii(key)}: {ascii(value)}," for key, value in sorted(data.items())]
        return lines + ["}", ""]

    notice = texts["LICENSE"].strip().replace('"""', "'''")
    lines = [
        f'"""Language data from GitHub Linguist {tag}, made by tools/update_linguist.py.',
        "",
        "Generated, do not edit.",
        "",
        "The data comes from https://github.com/github-linguist/linguist under this license:",
        "",
        notice,
        '"""',
        "",
        f"LINGUIST_VERSION = {ascii(tag)}",
        "",
        "# name: (type, color, group)",
        *mapping("LANGUAGES", table),
        "# lowercase name or alias: name",
        *mapping("ALIASES", aliases),
        "# lowercase extension: candidate names, those listing it first ahead",
        *mapping("EXTENSIONS", extensions_out),
        *mapping("FILENAMES", {k: tuple(v) for k, v in filenames.items()}),
        *mapping("INTERPRETERS", {k: tuple(v) for k, v in interpreters.items()}),
        "# lowercase extension: ((languages, condition), ...) tried in order",
        *mapping("HEURISTICS", {k: tuple(v) for k, v in rules.items()}),
        f"VENDOR = {ascii(tuple(vendor))}",
        "",
        f"DOCUMENTATION = {ascii(tuple(documentation))}",
    ]
    return "\n".join(lines) + "\n", dropped


def main() -> None:
    parser = argparse.ArgumentParser(
        description="generate vidi's language data from GitHub Linguist"
    )
    parser.add_argument("tag", help="Linguist release tag, for example v9.7.0")
    parser.add_argument("--source", type=Path, help="folder holding the YAML files and LICENSE")
    args = parser.parse_args()
    module, dropped = build(fetch(args.tag, args.source), args.tag)
    OUTPUT.write_text(module, encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT} ({len(module) // 1024} KB)")
    for line in dropped:
        print(f"skipped a pattern Python cannot compile: {line}", file=sys.stderr)


if __name__ == "__main__":
    main()
