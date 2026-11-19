"""Build the man pages and the README key tables from the app's own sources.

`git view --help` runs `man git-view`. Options come from the argparse parser
and the key tables from the help screen's SHORTCUTS.

Regenerate with `python -m vidi.docs` after changing either.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .cli import build_parser
from .screens.help import SHORTCUTS

ROOT = Path(__file__).resolve().parents[2]
MAN_DIR = ROOT / "man"
README = ROOT / "README.md"
PAGES = ("vidi", "git-view")
KEYS_START = "<!-- keys:start -->"
KEYS_END = "<!-- keys:end -->"


def _escape(text: str) -> str:
    text = text.replace("\\", "\\e").replace("-", "\\-")
    if text.startswith((".", "'")):
        text = "\\&" + text
    return text


def build_manpage(prog: str) -> str:
    parser = build_parser(prog)
    usage = " ".join(parser.format_usage().split()[1:])
    lines = [
        f'.TH {prog.upper()} 1 "" "vidi" "vidi manual"',
        ".SH NAME",
        f"{_escape(prog)} \\- {_escape(parser.description or '')}",
        ".SH SYNOPSIS",
        f".B {_escape(usage)}",
        ".SH DESCRIPTION",
        _escape(
            "Opens the repository you are in when given no path. "
            "Settings such as the theme and the language "
            "scan are changed inside the app from the command palette (Ctrl+P)."
        ),
        ".SH OPTIONS",
    ]
    for action in parser._actions:
        if action.help is argparse.SUPPRESS:
            continue
        name = ", ".join(action.option_strings) or action.metavar or action.dest
        lines += [".TP", f".B {_escape(name)}", _escape(action.help or "")]
    lines += [".SH KEYS", _escape("Press ? in the app to show these keys.")]
    for section, keys in SHORTCUTS:
        lines += [".SS " + _escape(section)]
        for key, description in keys:
            lines += [".TP", f".B {_escape(key)}", _escape(description)]
    lines += [
        ".SH FILES",
        ".TP",
        ".I ~/.config/vidi/config.json",
        _escape("Saved preferences (honors XDG_CONFIG_HOME)."),
        ".SH SEE ALSO",
        ".BR git (1)",
    ]
    return "\n".join(lines) + "\n"


def _markdown_keys(keys: str) -> str:
    """Put each key in backticks, as in "`j` / `k` or `up` / `down`"."""
    groups = []
    for group in keys.split("  or  "):
        names = [name if name == "type" else f"`{name}`" for name in group.split(" / ")]
        groups.append(" / ".join(names))
    return " or ".join(groups)


def build_readme_keys() -> str:
    lines = [KEYS_START]
    for section, keys in SHORTCUTS:
        lines += ["", f"### {section}", "", "| Key | Action |", "| --- | --- |"]
        lines += [f"| {_markdown_keys(key)} | {description} |" for key, description in keys]
    lines += ["", KEYS_END]
    return "\n".join(lines)


def update_readme(text: str) -> str:
    """The README text with its key tables regenerated between the markers."""
    start = text.index(KEYS_START)
    end = text.index(KEYS_END) + len(KEYS_END)
    return text[:start] + build_readme_keys() + text[end:]


def write_docs() -> None:
    MAN_DIR.mkdir(parents=True, exist_ok=True)
    for prog in PAGES:
        (MAN_DIR / f"{prog}.1").write_text(build_manpage(prog))
    README.write_text(update_readme(README.read_text()))


if __name__ == "__main__":
    write_docs()
