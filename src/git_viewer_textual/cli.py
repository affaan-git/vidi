"""CLI entrypoint."""

from __future__ import annotations

import argparse
import os
import sys

from .app import GitViewerApp
from .repository import build_repo_context


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a terminal-based local git repository viewer with Textual."
    )
    parser.add_argument("repo_dir", help="Path to local git repository")
    parser.add_argument(
        "--exclude-languages",
        default="",
        help="Comma-separated languages to exclude from language charts",
    )
    parser.add_argument(
        "--language-mode",
        choices=("bytes", "files"),
        default="bytes",
        help="Calculate language percentages by bytes or file count",
    )
    parser.add_argument(
        "--ignore-dirs",
        default="",
        help="Comma-separated additional directory names to ignore in language scanning",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_dir = os.path.abspath(args.repo_dir)
    if not os.path.isdir(repo_dir):
        print(f"Error: directory does not exist: {repo_dir}")
        sys.exit(1)
    if not os.path.isdir(os.path.join(repo_dir, ".git")):
        print(f"Error: not a git repository (missing .git): {repo_dir}")
        sys.exit(1)

    exclude_languages = {
        item.strip() for item in args.exclude_languages.split(",") if item.strip()
    }
    ignore_dirs = {item.strip() for item in args.ignore_dirs.split(",") if item.strip()}

    try:
        context, service = build_repo_context(
            repo_dir=repo_dir,
            language_mode=args.language_mode,
            exclude_languages=exclude_languages,
            extra_ignored_dirs=ignore_dirs,
        )
    except RuntimeError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    GitViewerApp(context, service).run()
