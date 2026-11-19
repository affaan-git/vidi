"""Deterministic sample repository for the test suite.

Rebuilds a small multi-language git repo in a temp dir with a pinned identity
and pinned dates.
"""
from __future__ import annotations

import atexit
import contextlib
import functools
import os
import shutil
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path

from vidi import config
from vidi.repository import build_initial_repo_context

_IDENTITY = "Example User"
_EMAIL = "example@example.com"

# (author date, message, {path: bytes}) for main, oldest first.
_COMMITS = [
    ('2024-01-15 10:00:00 -0600', 'initial project scaffold', {
        'README.md': b'# Example Repo\nRepository used for testing the local git viewer.\n',
        'src/main.py': b"print('hello from python')\n",
        'web/app.js': b"console.log('hello from js');\n",
        'web/styles.css': b'body { background: #0d1117; color: #c9d1d9; }\n',
    }),
    ('2024-03-03 14:30:00 -0600', 'add build script and python helper', {
        'README.md': b'# Example Repo\nRepository used for testing the local git viewer.\n',
        'scripts/build.sh': b'#!/usr/bin/env bash\necho "build step"\n',
        'src/main.py': b"print('hello from python')\n\ndef add(a, b):\n    return a + b\n",
        'web/app.js': b"console.log('hello from js');\n",
        'web/styles.css': b'body { background: #0d1117; color: #c9d1d9; }\n',
    }),
    ('2025-02-11 09:15:00 -0600', 'add config and sql schema', {
        'README.md': b'# Example Repo\nRepository used for testing the local git viewer.\n',
        'data/config.json': b'{\n  "name": "example-repo",\n  "version": 1,\n  "features": ["dash", "textual"]\n}\n',
        'docs/schema.sql': b'CREATE TABLE commits (\n  id INTEGER PRIMARY KEY,\n  hash TEXT NOT NULL,\n  message TEXT NOT NULL\n);\n',
        'scripts/build.sh': b'#!/usr/bin/env bash\necho "build step"\n',
        'src/main.py': b"print('hello from python')\n\ndef add(a, b):\n    return a + b\n",
        'web/app.js': b"console.log('hello from js');\n",
        'web/styles.css': b'body { background: #0d1117; color: #c9d1d9; }\n',
    }),
    ('2025-08-19 16:40:00 -0500', 'add html page and typescript util', {
        'README.md': b'# Example Repo\nRepository used for testing the local git viewer.\n',
        'data/config.json': b'{\n  "name": "example-repo",\n  "version": 1,\n  "features": ["dash", "textual"]\n}\n',
        'docs/schema.sql': b'CREATE TABLE commits (\n  id INTEGER PRIMARY KEY,\n  hash TEXT NOT NULL,\n  message TEXT NOT NULL\n);\n',
        'scripts/build.sh': b'#!/usr/bin/env bash\necho "build step"\n',
        'src/main.py': b"print('hello from python')\n\ndef add(a, b):\n    return a + b\n",
        'src/util.ts': b'export const greeting = (name: string) => `hello ${name}`;\n',
        'web/app.js': b"console.log('hello from js');\n",
        'web/index.html': b'<!doctype html>\n<html>\n  <head><title>Example</title></head>\n  <body><h1>Example Repo</h1></body>\n</html>\n',
        'web/styles.css': b'body { background: #0d1117; color: #c9d1d9; }\n',
    }),
    ('2026-01-07 11:05:00 -0600', 'update readme and add gitignore', {
        '.gitignore': b'__pycache__/\n.venv/\n',
        'README.md': b'# Example Repo\nRepository used for testing the local git viewer.\n\n## Notes\nThis repo has commits across multiple years for testing heatmaps.\n',
        'data/config.json': b'{\n  "name": "example-repo",\n  "version": 1,\n  "features": ["dash", "textual"]\n}\n',
        'docs/schema.sql': b'CREATE TABLE commits (\n  id INTEGER PRIMARY KEY,\n  hash TEXT NOT NULL,\n  message TEXT NOT NULL\n);\n',
        'scripts/build.sh': b'#!/usr/bin/env bash\necho "build step"\n',
        'src/main.py': b"print('hello from python')\n\ndef add(a, b):\n    return a + b\n",
        'src/util.ts': b'export const greeting = (name: string) => `hello ${name}`;\n',
        'web/app.js': b"console.log('hello from js');\n",
        'web/index.html': b'<!doctype html>\n<html>\n  <head><title>Example</title></head>\n  <body><h1>Example Repo</h1></body>\n</html>\n',
        'web/styles.css': b'body { background: #0d1117; color: #c9d1d9; }\n',
    }),
]

_EMPTY_COMMIT = ('2026-03-15 16:20:48 -0500', 'Newest Added Empty Commit')
_DEV_BRANCHES = ['dev2', 'dev3', 'dev4', 'dev5', 'dev6', 'dev7', 'dev8']


def build_sample_repo(path: str) -> None:
    """Create the sample repo at ``path``, which must be empty."""
    env = {
        **os.environ,
        'GIT_CONFIG_GLOBAL': os.devnull,
        'GIT_CONFIG_SYSTEM': os.devnull,
        'GIT_AUTHOR_NAME': _IDENTITY, 'GIT_AUTHOR_EMAIL': _EMAIL,
        'GIT_COMMITTER_NAME': _IDENTITY, 'GIT_COMMITTER_EMAIL': _EMAIL,
    }

    def git(*args, date=None):
        run_env = env if date is None else {**env, 'GIT_AUTHOR_DATE': date, 'GIT_COMMITTER_DATE': date}
        subprocess.run(['git', '-C', path, '-c', 'commit.gpgsign=false', *args],
                       env=run_env, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def write_tree(tree):
        for entry in os.listdir(path):
            if entry == '.git':
                continue
            target = os.path.join(path, entry)
            shutil.rmtree(target) if os.path.isdir(target) else os.remove(target)
        for rel, data in tree.items():
            dest = os.path.join(path, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, 'wb') as handle:
                handle.write(data)

    git('init', '-q', '-b', 'main')
    for date, message, tree in _COMMITS:
        write_tree(tree)
        git('add', '-A')
        git('commit', '-q', '-m', message, date=date)
    for branch in _DEV_BRANCHES:
        git('branch', branch)
    git('checkout', '-q', '-b', 'dev')
    empty_date, empty_message = _EMPTY_COMMIT
    git('commit', '-q', '--allow-empty', '-m', empty_message, date=empty_date)
    git('checkout', '-q', 'main')


@functools.lru_cache(maxsize=1)
def sample_repo() -> str:
    """Build the sample repo once and return its path, removed at exit."""
    path = tempfile.mkdtemp(prefix='vidi-sample-repo-')
    atexit.register(shutil.rmtree, path, ignore_errors=True)
    build_sample_repo(path)
    return path


def git(repo, *args, input=None, env=None, check=True):
    """Run git in a test repo with a fixed identity and signing off, and return its output."""
    return subprocess.run(
        ['git', '-C', repo, '-c', 'user.name=t', '-c', 'user.email=t@t', '-c', 'commit.gpgsign=false', *args],
        input=input, env=env, check=check, capture_output=True, text=True,
    ).stdout


def new_repo(parent):
    """An empty repo on branch main in a `repo` folder inside parent."""
    repo = os.path.join(parent, 'repo')
    os.makedirs(repo)
    git(repo, 'init', '-q', '-b', 'main')
    return repo


@contextlib.contextmanager
def temp_config():
    """Point the config module at an empty temporary folder."""
    orig_dir, orig_path = config.CONFIG_DIR, config.CONFIG_PATH
    try:
        with tempfile.TemporaryDirectory() as tmp:
            config.CONFIG_DIR = Path(tmp)
            config.CONFIG_PATH = config.CONFIG_DIR / 'config.json'
            yield
    finally:
        config.CONFIG_DIR, config.CONFIG_PATH = orig_dir, orig_path


def load_repo(repo_dir, *, language_mode='bytes', extra_ignored_dirs=()):
    """The context and service for a repo with the language scan and the current
    branch's history already loaded."""
    _, service = build_initial_repo_context(repo_dir, language_mode, set(), set(extra_ignored_dirs))
    context = service.refresh_context()
    branch_data = service.get_branch_data(context.current_branch)
    return replace(context, initial_branch_data=branch_data), service
