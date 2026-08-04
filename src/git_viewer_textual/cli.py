"""CLI entrypoint."""

from __future__ import annotations

import argparse
import os
import sys

from . import config
from .app import GitViewerApp
from .repository import build_initial_repo_context


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a terminal-based local git repository viewer with Textual."
    )
    parser.add_argument("repo_dir", help="Path to local git repository")
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

    # Language-scan settings live in the app (command palette -> "Language
    # settings"), persisted between runs -- not as CLI flags.
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

    GitViewerApp(context, service).run()
