"""Data processing helpers."""

from __future__ import annotations

import collections
import datetime as dt
from typing import Any

from .models import CommitFileChange, CommitPatchSection, CommitRecord
from .utils import utc_now


RANGE_PRESETS = [
    ("All time", "all"),
    ("1 year", "365"),
    ("90 days", "90"),
    ("30 days", "30"),
    ("7 days", "7"),
]

HEATMAP_STYLES = [
    "#0e4429",
    "#006d32",
    "#26a641",
    "#39d353",
]


def filter_commits(
    commits: list[CommitRecord],
    range_value: str,
    selected_year: int | None,
    *,
    now: dt.datetime | None = None,
) -> list[CommitRecord]:
    filtered = commits
    if range_value != "all" and filtered:
        days = int(range_value)
        window_end = utc_now() if now is None else now
        cutoff = window_end - dt.timedelta(days=days)
        filtered = [item for item in filtered if item.authored_at >= cutoff]
    if selected_year is not None:
        filtered = [item for item in filtered if item.authored_date.year == selected_year]
    return filtered


def aggregate_file_activity(commits: list[CommitRecord]) -> dict[str, dict[str, Any]]:
    file_activity: dict[str, dict[str, Any]] = {}
    for commit in commits:
        for path in commit.changed_files:
            info = file_activity.setdefault(
                path,
                {
                    "commits": 0,
                    "last_modified": commit.authored_at,
                    "authors": collections.Counter(),
                },
            )
            info["commits"] += 1
            if commit.authored_at > info["last_modified"]:
                info["last_modified"] = commit.authored_at
            info["authors"][commit.author_name] += 1
    return file_activity


def calc_streaks(counts: collections.Counter[dt.date]) -> tuple[int, int]:
    if not counts:
        return 0, 0
    days = sorted(day for day, value in counts.items() if value > 0)
    longest = 0
    current = 0
    previous = None
    for day in days:
        if previous is None or (day - previous).days == 1:
            current += 1
        else:
            current = 1
        longest = max(longest, current)
        previous = day
    latest = days[-1]
    running = 0
    day = latest
    while counts.get(day, 0) > 0:
        running += 1
        day -= dt.timedelta(days=1)
    return running, longest


def build_year_grid(
    commit_counts: collections.Counter[dt.date],
    year: int,
) -> tuple[list[list[int]], list[str], dt.date]:
    start = dt.date(year, 1, 1)
    end = dt.date(year, 12, 31)
    days_to_prev_sunday = (start.weekday() + 1) % 7
    first_sunday = start - dt.timedelta(days=days_to_prev_sunday)
    row_for_end = (end.weekday() + 1) % 7
    last_day = end + dt.timedelta(days=6 - row_for_end)
    total_days = (last_day - first_sunday).days + 1
    num_weeks = (total_days + 6) // 7
    grid = [[0 for _ in range(num_weeks)] for _ in range(7)]
    month_labels = ["   " for _ in range(num_weeks)]
    for idx in range(total_days):
        day = first_sunday + dt.timedelta(days=idx)
        col = idx // 7
        row = idx % 7
        grid[row][col] = commit_counts.get(day, 0)
        if day.day == 1 and day.year == year:
            month_labels[col] = day.strftime("%b")
    return grid, month_labels, first_sunday


def parse_patch_sections(patch_text: str) -> tuple[CommitPatchSection, ...]:
    sections: list[CommitPatchSection] = []
    if not patch_text.strip():
        return tuple(sections)

    current: list[str] = []
    for raw in patch_text.splitlines():
        if raw.startswith("diff --git ") and current:
            sections.append(_build_patch_section(current))
            current = [raw]
        else:
            current.append(raw)
    if current:
        sections.append(_build_patch_section(current))
    return tuple(sections)


def _build_patch_section(lines: list[str]) -> CommitPatchSection:
    old_path = ""
    new_path = ""
    is_binary = any("Binary files " in line for line in lines)
    is_rename = False

    for line in lines:
        if line.startswith("rename from "):
            old_path = line.removeprefix("rename from ").strip()
            is_rename = True
        elif line.startswith("rename to "):
            new_path = line.removeprefix("rename to ").strip()
            is_rename = True
        elif line.startswith("--- "):
            candidate = line[4:].strip()
            if candidate != "/dev/null":
                old_path = candidate.removeprefix("a/")
        elif line.startswith("+++ "):
            candidate = line[4:].strip()
            if candidate != "/dev/null":
                new_path = candidate.removeprefix("b/")

    if not old_path or not new_path:
        header = next((line for line in lines if line.startswith("diff --git ")), "")
        parts = header.split()
        if len(parts) >= 4:
            if not old_path:
                old_path = parts[2].removeprefix("a/")
            if not new_path:
                new_path = parts[3].removeprefix("b/")

    path = new_path or old_path or "unknown"
    return CommitPatchSection(
        path=path,
        old_path=old_path or path,
        new_path=new_path or path,
        patch="\n".join(lines).rstrip(),
        is_binary=is_binary,
        is_rename=is_rename or ((old_path or path) != (new_path or path)),
    )


def display_label_for_change(
    change: CommitFileChange,
    section: CommitPatchSection | None,
) -> str:
    if section and section.is_rename:
        label = f"{section.old_path} -> {section.new_path}"
    else:
        label = change.path
    if change.additions is None or change.deletions is None:
        label += " [binary]"
    return label
