"""Language detection that follows GitHub Linguist's data and rules."""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
from functools import lru_cache

from . import linguist_data as data

# Linguist counts only these types in a repository's languages unless a file is marked detectable.
COUNTED_TYPES = ("programming", "markup")
UNKNOWN_COLOR = "#cccccc"
# Linguist's heuristics read only this much of a file.
CONTENT_LIMIT = 50 * 1024
# Python's re holds the GIL and some Linguist patterns backtrack for minutes on crafted input.
# Content rules run in a separate process with this much time per file.
CONTENT_SECONDS = 1.0
CONTENT_TIMEOUTS = 3


# Kept separate, as one joined alternation searches many times slower.
_VENDOR = tuple(re.compile(pattern) for pattern in data.VENDOR)
_DOCUMENTATION = tuple(re.compile(pattern) for pattern in data.DOCUMENTATION)


@lru_cache(maxsize=65536)
def is_vendored(path: str) -> bool:
    return any(pattern.search(path) for pattern in _VENDOR)


@lru_cache(maxsize=65536)
def is_documentation(path: str) -> bool:
    return any(pattern.search(path) for pattern in _DOCUMENTATION)


def _extension(path: str) -> str | None:
    """The longest known extension of a path's file name, lowercase."""
    name = path.rsplit("/", 1)[-1].lower()
    position = name.find(".", 1)
    while position != -1:
        if name[position:] in data.EXTENSIONS:
            return name[position:]
        position = name.find(".", position + 1)
    return None


@lru_cache(maxsize=8192)
def candidates(path: str) -> tuple[str, ...]:
    """The languages a file name allows, by exact name first and then by extension."""
    name = path.rsplit("/", 1)[-1]
    if name in data.FILENAMES:
        return data.FILENAMES[name]
    extension = _extension(path)
    return data.EXTENSIONS[extension] if extension else ()


def needs_content(path: str, detectable: bool | None = None) -> bool:
    """Whether a file's contents can change its counted language.

    Content is skipped when every language it could settle on goes uncounted anyway.
    """
    found = candidates(path)
    if not found:
        return "." not in path.rsplit("/", 1)[-1][1:]
    if len(found) == 1:
        return False
    rules = data.HEURISTICS.get(_extension(path) or "", ())
    possible = set(found) | {language for languages, _ in rules for language in languages}
    return any(counted(language, detectable) for language in possible)


@lru_cache(maxsize=None)
def _compiled(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.MULTILINE)


def _holds(condition: tuple, text: str) -> bool:
    kind = condition[0]
    if kind == "all":
        return all(_holds(part, text) for part in condition[1])
    if kind == "match":
        return _compiled(condition[1]).search(text) is not None
    if kind == "absent":
        return _compiled(condition[1]).search(text) is None
    return True


def _from_shebang(text: str) -> str | None:
    first = text.split("\n", 1)[0]
    if not first.startswith("#!"):
        return None
    words = first[2:].split()
    if not words:
        return None
    command = words[0].rsplit("/", 1)[-1]
    if command == "env":
        rest = [word for word in words[1:] if not word.startswith("-") and "=" not in word]
        if not rest:
            return None
        command = rest[0].rsplit("/", 1)[-1]
    found = data.INTERPRETERS.get(command)
    if not found:
        found = data.INTERPRETERS.get(re.sub(r"[\d.]+$", "", command))
    return found[0] if found else None


def detect(path: str, content: str | None = None) -> str | None:
    """The language of a file, or None when Linguist would not recognize it.

    Ambiguous extensions are settled by Linguist's content rules, which still reach their
    fallback rule when the content is missing. A file without an extension is matched by its
    #! line. Anything else unresolved takes the first candidate.
    """
    found = candidates(path)
    if len(found) == 1:
        return found[0]
    if not found:
        return _from_shebang(content) if content else None
    text = content or ""
    for languages, condition in data.HEURISTICS.get(_extension(path) or "", ()):
        if _holds(condition, text):
            return languages[0]
    return found[0]


def serve() -> None:
    """Answer detection requests, one JSON [path, content] line each, until stdin closes."""
    for line in sys.stdin.buffer:
        path, content = json.loads(line)
        try:
            language = detect(path, content)
        except Exception:
            language = None
        sys.stdout.write(json.dumps(language) + "\n")
        sys.stdout.flush()


_SERVE = "import sys; sys.path.insert(0, sys.argv[1]); from vidi.languages import serve; serve()"
_CLOSED = object()


def _start_worker(items: list[tuple[str, str | None]]) -> tuple[subprocess.Popen, queue.Queue]:
    """A detection process with one thread feeding it `items` and one queueing its replies."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    process = subprocess.Popen(
        [sys.executable, "-I", "-c", _SERVE, root],
        cwd=root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    replies: queue.Queue = queue.Queue()

    def write() -> None:
        try:
            for path, content in items:
                process.stdin.write((json.dumps([path, content]) + "\n").encode("ascii"))
            process.stdin.close()
        except (OSError, ValueError):
            pass

    def read() -> None:
        try:
            for line in process.stdout:
                replies.put(json.loads(line))
        except (OSError, ValueError):
            pass
        replies.put(_CLOSED)

    threading.Thread(target=write, daemon=True).start()
    threading.Thread(target=read, daemon=True).start()
    return process, replies


def detect_contents(items: list[tuple[str, str | None]]) -> list[str | None]:
    """detect() for each (path, content), run in a separate process.

    A file that takes longer than CONTENT_SECONDS is detected without its content, and so is
    every file after CONTENT_TIMEOUTS of those.
    """
    results: list[str | None] = []
    timeouts = 0
    while len(results) < len(items):
        rest = items[len(results) :]
        if timeouts >= CONTENT_TIMEOUTS:
            results += [detect(path) for path, _ in rest]
            break
        try:
            process, replies = _start_worker(rest)
        except (OSError, ValueError):
            results += [detect(path) for path, _ in rest]
            break
        try:
            for path, _ in rest:
                try:
                    reply = replies.get(timeout=CONTENT_SECONDS)
                except queue.Empty:
                    reply = _CLOSED
                if reply is _CLOSED:
                    timeouts += 1
                    results.append(detect(path))
                    break
                results.append(reply)
        finally:
            process.kill()
            process.wait()
            for stream in (process.stdin, process.stdout):
                try:
                    stream.close()
                except OSError:
                    pass
    return results


def find(name: str | None) -> str | None:
    """The language a name or alias refers to, as a .gitattributes override writes it."""
    if not name:
        return None
    key = name.strip().lower()
    return data.ALIASES.get(key) or data.ALIASES.get(key.replace("-", " "))


def counted(language: str, detectable: bool | None = None) -> bool:
    """Whether a language is counted, unless a .gitattributes override says otherwise."""
    if detectable is not None:
        return detectable
    entry = data.LANGUAGES.get(language)
    return entry is not None and entry[0] in COUNTED_TYPES


def group(language: str) -> str:
    """The name a language is counted under, its group when it has one."""
    entry = data.LANGUAGES.get(language)
    return entry[2] if entry and entry[2] else language


def color(name: str) -> str:
    entry = data.LANGUAGES.get(name)
    return entry[1] if entry and entry[1] else UNKNOWN_COLOR
