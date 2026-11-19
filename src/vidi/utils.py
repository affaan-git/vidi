"""General utility helpers."""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import re


# Escape and C1 control characters in repo text are shown as a replacement character.
TERMINAL_CONTROL = re.compile(r"[\x1b\x7f-\x9f]")


def replace_terminal_control(text: str) -> str:
    return TERMINAL_CONTROL.sub("\ufffd", text)


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_iso_datetime(value: str) -> dt.datetime:
    """Parse a git ISO date and keep its own UTC offset, the author's local time."""
    # fromisoformat reads the "Z" that git writes for UTC only on Python 3.11 and later.
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return dt.datetime.fromisoformat(value)


_C_ESCAPES = {
    "a": 0x07,
    "b": 0x08,
    "t": 0x09,
    "n": 0x0A,
    "v": 0x0B,
    "f": 0x0C,
    "r": 0x0D,
    '"': 0x22,
    "\\": 0x5C,
}


def unquote_git_path(value: str) -> str:
    """Decode a path that git wrapped in double quotes with C-style escapes.

    A value without the surrounding quotes is returned unchanged.
    """
    if len(value) < 2 or not (value.startswith('"') and value.endswith('"')):
        return value
    body = value[1:-1]
    out = bytearray()
    index = 0
    while index < len(body):
        char = body[index]
        if char != "\\" or index + 1 == len(body):
            out += char.encode("utf-8")
            index += 1
            continue
        escape = body[index + 1]
        octal = body[index + 1 : index + 4]
        if len(octal) == 3 and all(digit in "01234567" for digit in octal):
            out.append(int(octal, 8) & 0xFF)
            index += 4
        elif escape in _C_ESCAPES:
            out.append(_C_ESCAPES[escape])
            index += 2
        else:
            out += escape.encode("utf-8")
            index += 2
    return replace_terminal_control(out.decode("utf-8", errors="replace"))


_MINUTE = 60
_HOUR = 60 * _MINUTE
_DAY = 24 * _HOUR
_WEEK = 7 * _DAY
_MONTH = 30 * _DAY
_YEAR = 365 * _DAY


def rel_time(value: dt.datetime, now: dt.datetime) -> str:
    seconds = int(max((now - value).total_seconds(), 0))
    if seconds < _MINUTE:
        return "just now"
    if seconds < _HOUR:
        return f"{seconds // _MINUTE}m ago"
    if seconds < _DAY:
        return f"{seconds // _HOUR}h ago"
    if seconds < _WEEK:
        return f"{seconds // _DAY}d ago"
    if seconds < _MONTH:
        return f"{seconds // _WEEK}w ago"
    if seconds < _YEAR:
        return f"{seconds // _MONTH}mo ago"
    return f"{seconds // _YEAR}y ago"


def truncate_middle(value: str, max_len: int) -> str:
    if len(value) <= max_len:
        return value
    if max_len <= 3:
        return value[:max_len]
    keep = max_len - 3
    head = keep // 2
    tail = keep - head
    return f"{value[:head]}...{value[-tail:]}"


def fuzzy_score(query: str, value: str) -> int | None:
    query = query.strip().lower()
    value = value.lower()
    if not query:
        return 0
    if query in value:
        return value.index(query)
    pos = -1
    gap_penalty = 0
    for char in query:
        next_pos = value.find(char, pos + 1)
        if next_pos == -1:
            return None
        if pos >= 0:
            gap_penalty += next_pos - pos - 1
        pos = next_pos
    return 100 + gap_penalty


def random_color(label: str) -> str:
    return f"#{hashlib.md5(label.encode('utf-8')).hexdigest()[:6]}"


# Languages of well-known extensionless or fixed filenames, keyed by lowercase name.
FILENAME_TO_LANGUAGE = {
    "dockerfile": "Dockerfile",
    "containerfile": "Dockerfile",
    "makefile": "Makefile",
    "gnumakefile": "Makefile",
    "cmakelists.txt": "CMake",
    "gemfile": "Ruby",
    "rakefile": "Ruby",
    "vagrantfile": "Ruby",
    "podfile": "Ruby",
    "jenkinsfile": "Groovy",
}


def detect_language(filename: str, extension_to_language: dict[str, str]) -> str:
    basename = os.path.basename(filename).lower()
    if basename in FILENAME_TO_LANGUAGE:
        return FILENAME_TO_LANGUAGE[basename]
    ext = os.path.splitext(filename)[1].lower()
    if ext in extension_to_language:
        return extension_to_language[ext]
    return ext[1:].upper() if ext else "Other"
