"""CLI entrypoint."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import textwrap
from importlib.metadata import PackageNotFoundError, version

from . import config
from .app import VidiApp
from .repository import build_initial_repo_context
from .screens.help import SHORTCUTS


def package_version() -> str:
    """The installed version in the package metadata from pyproject.toml."""
    try:
        return version("git-vidi")
    except PackageNotFoundError:
        return "unknown"


NAMES = "The same program runs as vidi, git view and git vidi."


def keys_reference(width: int = 79) -> str:
    """Every key section of the in-app help as plain text."""
    key_width = max(len(key) for _, keys in SHORTCUTS for key, _ in keys)
    indent = " " * (4 + key_width + 2)
    lines = ["keys:"]
    for section, keys in SHORTCUTS:
        lines.append(f"  {section}:")
        for key, description in keys:
            lines += textwrap.wrap(
                description,
                width,
                initial_indent=f"    {key:<{key_width}}  ",
                subsequent_indent=indent,
            )
    lines.append("")
    lines.append("Press ? in the app for the same keys.")
    lines.append(NAMES)
    return "\n".join(lines)


def build_parser(prog: str | None = None) -> argparse.ArgumentParser:
    """Description for the command line `-h` and the man page."""
    parser = argparse.ArgumentParser(
        prog=prog,
        description="browse a local git repository in the terminal",
        epilog=keys_reference(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"vidi {package_version()}",
    )
    parser.add_argument(
        "repo_dir",
        nargs="?",
        default=".",
        help="Path inside a local git repository (defaults to the current folder)",
    )
    return parser


def parse_args() -> argparse.Namespace:
    return build_parser().parse_args()


def resolve_repo_dir(path: str) -> str:
    """Return the top folder of the work tree that contains `path`.

    Works from any subfolder and worktrees and submodules.
    Raises ValueError with a message to print.
    """
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        raise ValueError(f"directory does not exist: {path}")
    try:
        output = subprocess.check_output(
            ["git", "-C", path, "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        output = b""
    # Git prints the path as the file system stores it and with forward slashes on Windows.
    top = os.fsdecode(output).strip()
    if not top:
        raise ValueError(f"not inside a git work tree: {path}")
    return os.path.normpath(top)


def main() -> None:
    args = parse_args()
    try:
        repo_dir = resolve_repo_dir(args.repo_dir)
    except ValueError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    settings = config.load_language_settings()

    try:
        context, service = build_initial_repo_context(
            repo_dir=repo_dir,
            language_mode=settings["language_mode"],
            exclude_languages=set(settings["exclude_languages"]),
            extra_ignored_dirs=set(settings["ignore_dirs"]),
        )
    except RuntimeError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    VidiApp(context, service).run()
