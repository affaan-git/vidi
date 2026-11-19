"""CLI entrypoint."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version

from . import config
from .app import VidiApp
from .repository import build_initial_repo_context


def package_version() -> str:
    """The installed version in the package metadata from pyproject.toml."""
    try:
        return version("vidi")
    except PackageNotFoundError:
        return "unknown"


def build_parser(prog: str | None = None) -> argparse.ArgumentParser:
    """Description for the command line `-h` and the man page."""
    parser = argparse.ArgumentParser(
        prog=prog,
        description="browse a local git repository in the terminal",
        epilog="Press ? in the app for the keys. For the full manual, run git view --help or man vidi",
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
        top = subprocess.check_output(
            ["git", "-C", path, "rev-parse", "--show-toplevel"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        top = ""
    if not top:
        raise ValueError(f"not inside a git work tree: {path}")
    return top


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
