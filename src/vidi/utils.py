"""General utility helpers."""

from __future__ import annotations

import datetime as dt
import hashlib
import os


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def parse_iso_datetime(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return parsed


_MINUTE = 60
_HOUR = 60 * _MINUTE
_DAY = 24 * _HOUR
_WEEK = 7 * _DAY
_MONTH = 4 * _WEEK  # approximate 28-day months and 336-day years
_YEAR = 12 * _MONTH


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


def detect_language(filename: str, extension_to_language: dict[str, str]) -> str:
    basename = os.path.basename(filename)
    if basename.lower() == "dockerfile":
        return "Dockerfile"
    ext = os.path.splitext(filename)[1].lower()
    if ext in extension_to_language:
        return extension_to_language[ext]
    return ext[1:].upper() if ext else "Other"
