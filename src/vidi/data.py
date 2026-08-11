"""Data processing helpers."""

from __future__ import annotations

import collections
import datetime as dt

from .models import CommitFileChange, CommitPatchSection, CommitRecord
from .utils import truncate_middle, utc_now


RANGE_PRESETS = [
    ("All time", "all"),
    ("1 year", "365"),
    ("90 days", "90"),
    ("30 days", "30"),
    ("7 days", "7"),
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


def resolve_graph_window(
    range_value: str,
    year: int,
    *,
    now: dt.datetime | None = None,
) -> tuple[dt.date, dt.date]:
    year_start = dt.date(year, 1, 1)
    year_end = dt.date(year, 12, 31)
    if range_value == "all":
        return year_start, year_end

    today = (utc_now() if now is None else now).date()
    if year != today.year:
        return year_start, year_end

    days = int(range_value)
    window_end = min(today, year_end)
    window_start = max(year_start, window_end - dt.timedelta(days=max(days - 1, 0)))
    return window_start, window_end


def build_date_grid(
    commit_counts: collections.Counter[dt.date],
    start: dt.date,
    end: dt.date,
) -> tuple[list[list[int | None]], list[str], dt.date]:
    days_to_prev_sunday = (start.weekday() + 1) % 7
    first_sunday = start - dt.timedelta(days=days_to_prev_sunday)
    row_for_end = (end.weekday() + 1) % 7
    last_day = end + dt.timedelta(days=6 - row_for_end)
    total_days = (last_day - first_sunday).days + 1
    num_weeks = (total_days + 6) // 7
    grid: list[list[int | None]] = [[None for _ in range(num_weeks)] for _ in range(7)]
    month_labels = ["   " for _ in range(num_weeks)]
    previous_visible_month: int | None = None

    for col in range(num_weeks):
        week_start = first_sunday + dt.timedelta(days=col * 7)
        visible_days: list[dt.date] = []
        for row in range(7):
            day = week_start + dt.timedelta(days=row)
            if start <= day <= end:
                grid[row][col] = commit_counts.get(day, 0)
                visible_days.append(day)
        if visible_days:
            first_visible_day = visible_days[0]
            if first_visible_day.month != previous_visible_month:
                month_labels[col] = first_visible_day.strftime("%b")
                previous_visible_month = first_visible_day.month
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


def section_for_change(
    sections: tuple[CommitPatchSection, ...],
    index: int,
    change: CommitFileChange,
) -> CommitPatchSection | None:
    """Find the patch section for a file change.

    Prefer the index-aligned section (file changes and patch sections come out
    of git in the same order), then fall back to a path match.
    """
    if 0 <= index < len(sections):
        candidate = sections[index]
        if candidate.path == change.path or candidate.new_path == change.path:
            return candidate
    return next(
        (
            section
            for section in sections
            if section.path == change.path or section.new_path == change.path
        ),
        None,
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


def compact_label_for_change(
    change: CommitFileChange,
    section: CommitPatchSection | None,
    max_len: int,
) -> str:
    full_label = display_label_for_change(change, section)
    if len(full_label) <= max_len:
        return full_label

    suffix = " [binary]" if change.additions is None or change.deletions is None else ""
    label_budget = max(max_len - len(suffix), 4)
    if section and section.is_rename:
        arrow = " -> "
        side_budget = max((label_budget - len(arrow)) // 2, 4)
        old_label = compact_path_label(section.old_path, side_budget)
        new_budget = max(label_budget - len(arrow) - len(old_label), 4)
        new_label = compact_path_label(section.new_path, new_budget)
        compact = f"{old_label}{arrow}{new_label}"
        if len(compact) > label_budget:
            compact = truncate_middle(compact, label_budget)
    else:
        compact = compact_path_label(change.path, label_budget)

    return f"{compact}{suffix}"


def _ellipsize_filename(name: str, max_len: int) -> str:
    """Shorten a filename to fit, keeping its (distinctive) start and, when there
    is room, its extension, with a single ellipsis: commit_files.py -> commit_f….py."""
    if len(name) <= max_len:
        return name
    if max_len <= 1:
        return "…"
    dot = name.rfind(".")
    ext = name[dot:] if 0 < dot < len(name) - 1 else ""
    if ext and len(ext) + 2 <= max_len:
        return f"{name[:max_len - 1 - len(ext)]}…{ext}"
    return f"{name[:max_len - 1]}…"


def compact_path_label(path: str, max_len: int) -> str:
    if len(path) <= max_len:
        return path
    parts = [part for part in path.split("/") if part]
    if not parts:
        return _ellipsize_filename(path, max_len)

    filename = parts[-1]
    # No room to prefix a parent dir ("…/parent/file"), so show the filename
    # alone, truncated -- never a bare "…/" (misleading, esp. for root files).
    if len(filename) + 2 > max_len:
        return _ellipsize_filename(filename, max_len)

    label = filename
    for parent in reversed(parts[:-1]):
        candidate = f"{parent}/{label}"
        if len(candidate) + 2 > max_len:
            break
        label = candidate
    if label == path:
        return label
    return f"…/{label}"
