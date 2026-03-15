#!/usr/bin/env python3
"""Terminal-first local git repository viewer built with Textual."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import os
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from rich.console import Group
from rich.style import Style
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.color import Color
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import DataTable, Footer, Header, Input, Static


DEFAULT_IGNORED_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "target",
    "coverage",
    ".next",
    ".nuxt",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".idea",
    ".vscode",
}

EXTENSION_TO_LANGUAGE = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".html": "HTML",
    ".htm": "HTML",
    ".css": "CSS",
    ".java": "Java",
    ".c": "C",
    ".h": "C/C++ Header",
    ".cpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    ".hpp": "C++ Header",
    ".cs": "C#",
    ".rb": "Ruby",
    ".go": "Go",
    ".php": "PHP",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".rs": "Rust",
    ".lua": "Lua",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
    ".fish": "Shell",
    ".json": "JSON",
    ".jsonc": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".xml": "XML",
    ".toml": "TOML",
    ".md": "Markdown",
    ".sql": "SQL",
    ".r": "R",
    ".dart": "Dart",
    ".scala": "Scala",
    ".pl": "Perl",
    ".pm": "Perl",
    ".m": "Objective-C/MATLAB",
    ".mm": "Objective-C++",
    ".vb": "Visual Basic",
    ".ps1": "PowerShell",
    ".bat": "Batchfile",
    ".cmd": "Batchfile",
    ".dockerfile": "Dockerfile",
}

LANGUAGE_COLORS = {
    "Python": "#3572A5",
    "JavaScript": "#f1e05a",
    "HTML": "#e34c26",
    "CSS": "#563d7c",
    "Java": "#b07219",
    "C": "#555555",
    "C++": "#f34b7d",
    "C#": "#178600",
    "Ruby": "#701516",
    "Go": "#00ADD8",
    "PHP": "#4F5D95",
    "TypeScript": "#3178c6",
    "Swift": "#ffac45",
    "Kotlin": "#A97BFF",
    "Rust": "#dea584",
    "Lua": "#000080",
    "Shell": "#89e051",
    "JSON": "#292929",
    "YAML": "#cb171e",
    "XML": "#0060ac",
    "Markdown": "#083fa1",
    "SQL": "#e38c00",
    "R": "#198CE7",
    "Dart": "#00B4AB",
    "Scala": "#c22d40",
    "Perl": "#0298c3",
    "PowerShell": "#012456",
    "Batchfile": "#C1F12E",
    "Dockerfile": "#384d54",
}

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


@dataclass(frozen=True)
class ThemePalette:
    background: Color
    panel: Color
    surface: Color
    foreground: Color
    muted: Color
    border: Color
    primary: Color
    selected_bg: Color
    selected_fg: Color
    overlay: Color


def make_style(
    foreground: Color | None = None,
    *,
    background: Color | None = None,
    bold: bool = False,
    dim: bool = False,
) -> Style:
    return Style(
        color=foreground.rich_color if foreground is not None else None,
        bgcolor=background.rich_color if background is not None else None,
        bold=bold,
        dim=dim,
    )


def resolve_theme_palette(theme: Theme) -> ThemePalette:
    primary = Color.parse(theme.primary)

    if theme.foreground is not None:
        foreground = Color.parse(theme.foreground)
    elif theme.background is not None:
        foreground = Color.parse(theme.background).get_contrast_text()
    else:
        foreground = primary.get_contrast_text()

    if theme.background is not None:
        background = Color.parse(theme.background)
    elif theme.dark:
        background = foreground.blend(primary, 0.08).darken(0.82)
    else:
        background = foreground.get_contrast_text().blend(primary, 0.05)

    if theme.panel is not None:
        panel = Color.parse(theme.panel)
    elif theme.surface is not None:
        source = Color.parse(theme.surface)
        panel = source.lighten(0.02) if theme.dark else source.darken(0.03)
    else:
        panel = background.lighten(0.06) if theme.dark else background.darken(0.06)

    if theme.surface is not None:
        surface = Color.parse(theme.surface)
    elif theme.panel is not None:
        source = Color.parse(theme.panel)
        surface = source.darken(0.02) if theme.dark else source.lighten(0.03)
    else:
        surface = background.lighten(0.10) if theme.dark else background.darken(0.03)

    muted = foreground.blend(panel, 0.50 if theme.dark else 0.35)
    border = panel.blend(foreground, 0.18 if theme.dark else 0.35)
    selected_bg = primary.blend(surface, 0.42 if theme.dark else 0.60)
    selected_fg = selected_bg.get_contrast_text()
    overlay = background.darken(0.35 if theme.dark else 0.75).with_alpha(
        0.68 if theme.dark else 0.50
    )

    return ThemePalette(
        background=background,
        panel=panel,
        surface=surface,
        foreground=foreground,
        muted=muted,
        border=border,
        primary=primary,
        selected_bg=selected_bg,
        selected_fg=selected_fg,
        overlay=overlay,
    )


def run_git(repo_dir: str, args: list[str]) -> str:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=repo_dir, text=True, stderr=subprocess.PIPE
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else "unknown git error"
        raise RuntimeError(f"git {' '.join(args)} failed: {stderr}") from exc


def parse_iso_datetime(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return parsed


def rel_time(value: dt.datetime, now: dt.datetime) -> str:
    delta = now - value
    seconds = int(max(delta.total_seconds(), 0))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    if seconds < 604800:
        return f"{seconds // 86400}d ago"
    if seconds < 2419200:
        return f"{seconds // 604800}w ago"
    if seconds < 29030400:
        return f"{seconds // 2419200}mo ago"
    return f"{seconds // 29030400}y ago"


def truncate_middle(value: str, max_len: int) -> str:
    if len(value) <= max_len:
        return value
    if max_len <= 3:
        return value[:max_len]
    keep = max_len - 3
    head = keep // 2
    tail = keep - head
    return f"{value[:head]}...{value[-tail:]}"


def detect_language(filename: str) -> str:
    basename = os.path.basename(filename)
    if basename.lower() == "dockerfile":
        return "Dockerfile"
    ext = os.path.splitext(filename)[1].lower()
    if ext in EXTENSION_TO_LANGUAGE:
        return EXTENSION_TO_LANGUAGE[ext]
    return ext[1:].upper() if ext else "Other"


def random_color(label: str) -> str:
    return f"#{hashlib.md5(label.encode('utf-8')).hexdigest()[:6]}"


@dataclass(frozen=True)
class CommitRecord:
    commit_hash: str
    authored_at: dt.datetime
    message: str
    author_name: str
    author_email: str
    changed_files: tuple[str, ...]

    @property
    def authored_date(self) -> dt.date:
        return self.authored_at.date()


@dataclass
class BranchData:
    branch: str
    commits: list[CommitRecord]
    years: list[int]
    contributors: collections.Counter[str]
    file_activity: dict[str, dict[str, Any]]


@dataclass
class RepoContext:
    repo_dir: str
    project_name: str
    language_mode: str
    exclude_languages: set[str]
    ignore_dirs: set[str]
    branches: list[str]
    current_branch: str
    language_stats: list[dict[str, Any]]
    files_by_language: dict[str, list[str]]
    about_text: str


class RepoService:
    def __init__(
        self,
        repo_dir: str,
        language_mode: str,
        exclude_languages: set[str],
        ignore_dirs: set[str],
    ):
        self.repo_dir = repo_dir
        self.language_mode = language_mode
        self.exclude_languages = exclude_languages
        self.ignore_dirs = ignore_dirs

    @lru_cache(maxsize=64)
    def get_branch_data(self, branch: str) -> BranchData:
        meta_out = run_git(
            self.repo_dir,
            [
                "log",
                branch,
                "--date=iso-strict",
                "--pretty=format:%H|%ad|%an|%ae|%s",
            ],
        )
        files_out = run_git(
            self.repo_dir,
            [
                "log",
                branch,
                "--pretty=format:__COMMIT__%H",
                "--name-only",
            ],
        )

        files_by_commit: dict[str, list[str]] = {}
        current_hash = ""
        for raw in files_out.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("__COMMIT__"):
                current_hash = line.removeprefix("__COMMIT__")
                files_by_commit.setdefault(current_hash, [])
                continue
            if current_hash:
                files_by_commit[current_hash].append(line)

        commits: list[CommitRecord] = []
        contributors: collections.Counter[str] = collections.Counter()
        file_activity: dict[str, dict[str, Any]] = {}

        for line in meta_out.splitlines():
            if not line.strip():
                continue
            parts = line.split("|", 4)
            if len(parts) != 5:
                continue
            commit_hash, authored_raw, author_name, author_email, message = parts
            authored_at = parse_iso_datetime(authored_raw)
            changed_files = tuple(files_by_commit.get(commit_hash, []))
            record = CommitRecord(
                commit_hash=commit_hash,
                authored_at=authored_at,
                message=message.strip(),
                author_name=author_name.strip(),
                author_email=author_email.strip(),
                changed_files=changed_files,
            )
            commits.append(record)
            contributors[record.author_name] += 1
            for path in changed_files:
                info = file_activity.setdefault(
                    path,
                    {
                        "commits": 0,
                        "last_modified": record.authored_at,
                        "authors": collections.Counter(),
                    },
                )
                info["commits"] += 1
                if record.authored_at > info["last_modified"]:
                    info["last_modified"] = record.authored_at
                info["authors"][record.author_name] += 1

        years = sorted({item.authored_date.year for item in commits})
        return BranchData(branch, commits, years, contributors, file_activity)


def get_branches(repo_dir: str) -> tuple[list[str], str]:
    output = run_git(repo_dir, ["branch", "--format=%(refname:short)"])
    branches = [line.strip() for line in output.splitlines() if line.strip()]
    head = run_git(repo_dir, ["rev-parse", "--abbrev-ref", "HEAD"]).strip()
    if head and head not in branches:
        branches.append(head)
    branches = sorted(set(branches))
    if not branches:
        raise RuntimeError("No branches found in repository.")
    return branches, head if head else branches[0]


def parse_gitignore(repo_dir: str) -> set[str]:
    path = Path(repo_dir) / ".gitignore"
    names: set[str] = set()
    if not path.exists():
        return names
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "/" in line or "*" in line or line.startswith("!"):
            continue
        names.add(line.rstrip("/"))
    return names


def extract_about_text(repo_dir: str) -> str:
    for candidate in ("README.md", "README.rst", "README.txt", "readme.md"):
        file_path = Path(repo_dir) / candidate
        if not file_path.exists():
            continue
        content = file_path.read_text(encoding="utf-8", errors="ignore").strip()
        if not content:
            continue
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        for line in lines:
            if not line.startswith("#"):
                return line[:220]
    return "Local repository analytics dashboard."


def scan_languages(
    repo_dir: str,
    language_mode: str,
    exclude_languages: set[str],
    ignore_dirs: set[str],
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    totals: dict[str, int] = {}
    files_by_language: dict[str, list[str]] = {}
    total_value = 0

    for root, dirs, files in os.walk(repo_dir):
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        for filename in files:
            full_path = os.path.join(root, filename)
            if not os.path.isfile(full_path):
                continue
            language = detect_language(filename)
            if language in exclude_languages:
                continue
            rel_path = os.path.relpath(full_path, repo_dir)
            files_by_language.setdefault(language, []).append(rel_path)
            if language_mode == "files":
                value = 1
            else:
                try:
                    value = os.path.getsize(full_path)
                except OSError:
                    value = 0
            totals[language] = totals.get(language, 0) + value
            total_value += value

    stats: list[dict[str, Any]] = []
    for language, value in totals.items():
        percent = (value / total_value * 100.0) if total_value else 0.0
        stats.append(
            {
                "language": language,
                "value": value,
                "percentage": percent,
                "color": LANGUAGE_COLORS.get(language, random_color(language)),
            }
        )
    stats.sort(key=lambda item: item["percentage"], reverse=True)
    for language in files_by_language:
        files_by_language[language].sort()
    return stats, files_by_language


def build_repo_context(
    repo_dir: str,
    language_mode: str,
    exclude_languages: set[str],
    extra_ignored_dirs: set[str],
) -> tuple[RepoContext, RepoService]:
    branches, current_branch = get_branches(repo_dir)
    ignore_dirs = set(DEFAULT_IGNORED_DIRS)
    ignore_dirs.update(parse_gitignore(repo_dir))
    ignore_dirs.update(extra_ignored_dirs)
    language_stats, files_by_language = scan_languages(
        repo_dir, language_mode, exclude_languages, ignore_dirs
    )
    context = RepoContext(
        repo_dir=repo_dir,
        project_name=os.path.basename(os.path.abspath(repo_dir)),
        language_mode=language_mode,
        exclude_languages=exclude_languages,
        ignore_dirs=ignore_dirs,
        branches=branches,
        current_branch=current_branch,
        language_stats=language_stats,
        files_by_language=files_by_language,
        about_text=extract_about_text(repo_dir),
    )
    return context, RepoService(repo_dir, language_mode, exclude_languages, ignore_dirs)


def filter_commits(
    commits: list[CommitRecord], range_value: str, selected_year: int | None
) -> list[CommitRecord]:
    filtered = commits
    if range_value != "all" and filtered:
        days = int(range_value)
        max_time = max(item.authored_at for item in filtered)
        cutoff = max_time - dt.timedelta(days=days)
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
    commit_counts: collections.Counter[dt.date], year: int
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


def render_contribution_graph(
    commit_counts: collections.Counter[dt.date],
    year: int,
    selected_day: dt.date | None,
    palette: ThemePalette,
) -> Group:
    grid, months, first_sunday = build_year_grid(commit_counts, year)
    month_line = [" "] * len(months)
    last_end = -4
    for index, label in enumerate(months):
        if not label.strip():
            continue
        short = label[:3]
        if index < last_end:
            continue
        for offset, char in enumerate(short):
            pos = index + offset
            if pos < len(month_line):
                month_line[pos] = char
        last_end = index + len(short) + 1

    header = Text("    " + "".join(month_line), style=make_style(palette.muted))

    lines: list[Text] = [header]
    day_labels = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    max_value = max((value for row in grid for value in row), default=0)
    inactive_style = palette.surface.blend(
        palette.foreground, 0.10 if palette.background.brightness < 0.5 else 0.06
    ).hex6
    heatmap_styles = [inactive_style, *HEATMAP_STYLES]
    cell_char = "■"
    selected_char = "▣"

    for row_index, day_name in enumerate(day_labels):
        line = Text(f"{day_name} ", style=make_style(palette.muted))
        for col_index, value in enumerate(grid[row_index]):
            if value <= 0:
                style = heatmap_styles[0]
            elif max_value <= 1:
                style = heatmap_styles[-1]
            else:
                ratio = value / max_value
                bucket = min(4, max(1, int(round(ratio * 4))))
                style = heatmap_styles[bucket]
            cell_day = first_sunday + dt.timedelta(days=col_index * 7 + row_index)
            if selected_day == cell_day:
                line.append(selected_char, style=f"bold {style}")
            else:
                line.append(cell_char, style=style)
        lines.append(line)

    legend = Text("Less ", style=make_style(palette.muted, bold=True))
    for style in heatmap_styles:
        legend.append(cell_char, style=style)
    legend.append(" More", style=make_style(palette.muted, bold=True))
    return Group(*lines, legend)


def build_stats_text(
    branch: str, year: int, commits: list[CommitRecord], palette: ThemePalette
) -> Text:
    counts: collections.Counter[dt.date] = collections.Counter(
        commit.authored_date for commit in commits
    )
    active_days = len(counts)
    current_streak, longest_streak = calc_streaks(counts)
    avg_per_day = len(commits) / active_days if active_days else 0.0
    latest = commits[0].authored_at if commits else None
    latest_text = rel_time(latest, dt.datetime.utcnow()) if latest else "n/a"

    text = Text()
    text.append(f"Branch: {branch}\n", style=make_style(palette.foreground, bold=True))
    text.append(f"Year: {year}\n", style=make_style(palette.muted))
    text.append(f"Commits: {len(commits)}\n", style=make_style(palette.muted))
    text.append(f"Active days: {active_days}\n", style=make_style(palette.muted))
    text.append(f"Avg/day: {avg_per_day:.2f}\n", style=make_style(palette.muted))
    text.append(f"Current streak: {current_streak}d\n", style=make_style(palette.muted))
    text.append(f"Longest streak: {longest_streak}d\n", style=make_style(palette.muted))
    text.append(f"Last commit: {latest_text}", style=make_style(palette.muted))
    return text


def build_summary_text(
    project_name: str,
    branch: str,
    year: int,
    commits: list[CommitRecord],
    language_stats: list[dict[str, Any]],
    palette: ThemePalette,
) -> Text:
    counts: collections.Counter[dt.date] = collections.Counter(commit.authored_date for commit in commits)
    top_language = language_stats[0]["language"] if language_stats else "Other"
    text = Text()
    text.append(project_name, style=make_style(palette.foreground, bold=True))
    text.append("  |  ", style=make_style(palette.muted))
    text.append(f"branch {branch}", style=make_style(palette.muted))
    text.append("  |  ", style=make_style(palette.muted))
    text.append(f"year {year}", style=make_style(palette.muted))
    text.append("  |  ", style=make_style(palette.muted))
    text.append(f"{len(commits)} commits", style=make_style(palette.muted))
    text.append("  |  ", style=make_style(palette.muted))
    text.append(f"{len(counts)} active days", style=make_style(palette.muted))
    text.append("  |  ", style=make_style(palette.muted))
    text.append(f"top language {top_language}", style=make_style(palette.muted))
    return text


def build_graph_title(branch: str, range_value: str, year: int, palette: ThemePalette) -> Text:
    range_label = dict(RANGE_PRESETS).get(range_value, range_value)
    status = Text()
    status.append(branch, style=make_style(palette.foreground))
    status.append(" · ", style=make_style(palette.muted))
    status.append(range_label, style=make_style(palette.foreground))
    status.append(" · ", style=make_style(palette.muted))
    status.append(str(year), style=make_style(palette.foreground, bold=True))

    title = Table.grid(expand=True)
    title.add_column(ratio=1)
    title.add_column(justify="right")
    title.add_row(
        Text("Contribution Activity", style=make_style(palette.muted, bold=True)),
        status,
    )
    return title


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


def build_language_summary(
    language_stats: list[dict[str, Any]], selected_language: str, palette: ThemePalette
) -> Group:
    bar = Text()
    for item in language_stats[:8]:
        width = max(1, int(round(item["percentage"] / 4)))
        marker = "█" * min(width, 18)
        style = item["color"]
        if item["language"] == selected_language:
            style = f"bold {style}"
        bar.append(marker, style=style)

    legend_lines: list[Text] = []
    for item in language_stats[:4]:
        line = Text()
        prefix = ">" if item["language"] == selected_language else " "
        line.append(prefix + " ", style=make_style(palette.muted))
        line.append("● ", style=item["color"])
        label_style = (
            make_style(palette.foreground, bold=True)
            if item["language"] == selected_language
            else make_style(palette.foreground)
        )
        line.append(item["language"], style=label_style)
        line.append(f" {item['percentage']:.1f}%", style=make_style(palette.muted))
        legend_lines.append(line)

    return Group(bar, *legend_lines)


class BranchPickerScreen(ModalScreen[str | None]):
    CSS = """
    BranchPickerScreen {
        align: center middle;
    }

    #branch-picker {
        width: 52;
        height: 17;
        padding: 1 1;
    }

    #branch-picker-title {
        text-style: bold;
        margin: 0 1;
    }

    #branch-picker-subtitle {
        display: none;
    }

    #branch-picker-help {
        margin: 1 1 0 1;
        content-align: right middle;
        height: 1;
    }

    #branch-picker-filter {
        margin: 0 1 1 1;
        border: solid $border-blurred;
    }

    #branch-picker-filter:focus {
        border: solid $border;
    }

    #branch-picker-list {
        height: 3;
        margin: 0 1;
        padding: 0 1;
    }
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("up", "cursor_up", "Up"),
        ("down", "cursor_down", "Down"),
    ]

    def __init__(self, branches: list[str], current_branch: str) -> None:
        super().__init__()
        self.branches = branches
        self.current_branch = current_branch
        self.highlighted_branch = current_branch
        self.filtered_branches = branches[:]
        self._row_style = Style()
        self._selected_row_style = Style(reverse=True, bold=True)
        self._muted_style = Style(dim=True)
        self._current_tag_style = Style(bold=True)
        self._selected_current_tag_style = Style(reverse=True, bold=True)
        self._max_visible_rows = 4
        self._branch_row_width = 40
        self._branch_current_width = len("Current")
        self._branch_name_width = 32

    def compose(self) -> ComposeResult:
        with Vertical(id="branch-picker"):
            yield Static("Select Branch  /  type to filter", id="branch-picker-title")
            yield Static("Type to filter. Up/Down move the selection.", id="branch-picker-subtitle")
            yield Input(placeholder="Filter branches", id="branch-picker-filter")
            yield Static(id="branch-picker-list")
            yield Static("Enter choose   Esc cancel", id="branch-picker-help")

    def on_mount(self) -> None:
        self.watch(self.app, "theme", self.on_theme_change, init=False)
        self._apply_theme()
        self._freeze_branch_layout()
        self._refresh_branch_list()
        self.query_one("#branch-picker-filter", Input).focus()

    def on_theme_change(self, old_value: str, new_value: str) -> None:
        self._apply_theme()
        self._refresh_branch_list()

    def _apply_theme(self) -> None:
        palette = resolve_theme_palette(self.app.current_theme)

        self.styles.background = palette.overlay

        picker = self.query_one("#branch-picker", Vertical)
        picker.styles.background = palette.panel
        picker.styles.color = palette.foreground
        picker.styles.border = ("solid", palette.border)

        help_text = self.query_one("#branch-picker-help", Static)
        help_text.styles.color = palette.muted

        list_widget = self.query_one("#branch-picker-list", Static)
        list_widget.styles.background = palette.surface
        list_widget.styles.border = ("solid", palette.border)

        self._row_style = make_style(palette.foreground)
        self._selected_row_style = make_style(
            palette.selected_fg,
            background=palette.selected_bg,
            bold=True,
        )
        self._muted_style = make_style(palette.muted, dim=True)
        self._current_tag_style = make_style(palette.primary, bold=True)
        self._selected_current_tag_style = make_style(
            palette.selected_fg,
            background=palette.selected_bg,
            bold=True,
        )

    def _freeze_branch_layout(self) -> None:
        widget = self.query_one("#branch-picker-list", Static)
        available_width = widget.content_region.width or widget.content_size.width
        if not available_width:
            available_width = widget.size.width - 4 if widget.size.width > 4 else 40
        self._branch_row_width = max(24, available_width)
        self._branch_name_width = max(8, self._branch_row_width - self._branch_current_width - 1)

    def _refresh_branch_list(self) -> None:
        widget = self.query_one("#branch-picker-list", Static)
        lines = Text()
        if not self.filtered_branches:
            self.highlighted_branch = ""
            widget.styles.height = 3
            lines.append("No matching branches".ljust(self._branch_row_width), style=self._muted_style)
            widget.update(lines)
            return
        if self.highlighted_branch not in self.filtered_branches:
            self.highlighted_branch = self.filtered_branches[0]
        total = len(self.filtered_branches)
        selected_index = self.filtered_branches.index(self.highlighted_branch)
        content_rows = min(self._max_visible_rows, total)
        widget.styles.height = max(3, content_rows + 2)

        start = max(0, selected_index - (content_rows // 2))
        end = min(total, start + content_rows)
        start = max(0, end - content_rows)

        for branch in self.filtered_branches[start:end]:
            is_current = branch == self.current_branch
            is_highlighted = branch == self.highlighted_branch
            row_style = self._selected_row_style if is_highlighted else self._row_style
            branch_text = truncate_middle(branch, self._branch_name_width)
            suffix = "\n" if branch != self.filtered_branches[end - 1] or end < len(self.filtered_branches) else ""
            lines.append(branch_text.ljust(self._branch_name_width), style=row_style)
            lines.append(" ", style=row_style)
            if is_current:
                current_style = (
                    self._selected_current_tag_style if is_highlighted else self._current_tag_style
                )
                lines.append("Current".rjust(self._branch_current_width), style=current_style)
            else:
                lines.append(" " * self._branch_current_width, style=row_style)
            lines.append(suffix)
        widget.update(lines)

    def _move_highlight(self, direction: int) -> None:
        if not self.filtered_branches:
            self.highlighted_branch = ""
            return
        if self.highlighted_branch not in self.filtered_branches:
            self.highlighted_branch = self.filtered_branches[0]
        else:
            idx = self.filtered_branches.index(self.highlighted_branch)
            idx = max(0, min(len(self.filtered_branches) - 1, idx + direction))
            self.highlighted_branch = self.filtered_branches[idx]
        self._refresh_branch_list()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "branch-picker-filter":
            return
        query = event.value
        ranked: list[tuple[int, str]] = []
        for branch in self.branches:
            score = fuzzy_score(query, branch)
            if score is not None:
                ranked.append((score, branch))
        ranked.sort(key=lambda item: (item[0], item[1]))
        self.filtered_branches = [branch for _score, branch in ranked]
        self.highlighted_branch = self.filtered_branches[0] if self.filtered_branches else ""
        self._refresh_branch_list()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "branch-picker-filter":
            return
        if self.filtered_branches and self.highlighted_branch in self.filtered_branches:
            self.dismiss(self.highlighted_branch)

    def action_cursor_up(self) -> None:
        if self.query_one("#branch-picker-filter", Input).has_focus:
            self._move_highlight(-1)

    def action_cursor_down(self) -> None:
        if self.query_one("#branch-picker-filter", Input).has_focus:
            self._move_highlight(1)

    def action_cancel(self) -> None:
        self.dismiss(None)


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


class GitViewerTUI(App[None]):
    CSS = """
    Screen {
    }

    #summary-bar {
        display: none;
        padding: 0 1;
    }

    #body {
        layout: vertical;
        height: 1fr;
        margin: 1 1 1 1;
    }

    .body-row {
        layout: horizontal;
        height: 1fr;
    }

    .body-row.top {
        margin-bottom: 1;
    }

    .left-column {
        width: 2fr;
        margin-right: 1;
    }

    .right-column {
        width: 1fr;
    }

    .panel {
        margin-bottom: 1;
        padding: 1 1;
    }

    .panel-title {
        text-style: bold;
        margin-bottom: 0;
    }

    #day-details-scroll {
        height: 1fr;
        overflow-y: auto;
        scrollbar-gutter: stable;
    }

    #day-scroll-hint {
        height: 1;
        content-align: right middle;
    }

    #languages-panel {
        height: 1fr;
    }

    #language-summary {
    }

    #day-details {
        height: auto;
        min-height: 1;
    }

    #language-files {
    }

    DataTable {
        height: 1fr;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("b", "open_branch_picker", "Branches"),
        ("[", "prev_year", "Prev Year"),
        ("]", "next_year", "Next Year"),
        ("r", "cycle_range", "Cycle Range"),
        ("j", "day_down", "Next Day"),
        ("k", "day_up", "Prev Day"),
        ("n", "next_language", "Next Language"),
        ("p", "prev_language", "Prev Language"),
    ]

    selected_branch: reactive[str]
    selected_range: reactive[str]
    selected_year: reactive[int]
    selected_day: reactive[dt.date | None]
    selected_language: reactive[str]
    selected_commit_hash: reactive[str | None]

    def __init__(self, context: RepoContext, service: RepoService) -> None:
        super().__init__()
        self.context = context
        self.service = service
        self._theme_palette = resolve_theme_palette(self.current_theme)
        self.selected_branch = context.current_branch
        self.selected_range = "all"
        initial_branch = service.get_branch_data(context.current_branch)
        self.selected_year = max(initial_branch.years) if initial_branch.years else dt.date.today().year
        self.selected_day = None
        self.selected_language = (
            context.language_stats[0]["language"] if context.language_stats else "Other"
        )
        self.selected_commit_hash = None
        self._day_keys: list[dt.date] = []
        self._language_keys: list[str] = [item["language"] for item in context.language_stats]
        self._suspend_table_events = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Static(id="summary-bar"):
            yield Static(id="summary-content")

        with Vertical(id="body"):
            with Horizontal(classes="body-row top"):
                with Vertical(classes="left-column"):
                    with Vertical(id="graph-panel", classes="panel"):
                        yield Static("Contribution Activity", id="graph-title", classes="panel-title")
                        yield Static(id="graph-content")
                with Vertical(classes="right-column"):
                    with Vertical(id="details-panel", classes="panel"):
                        yield Static("Commit Details", id="details-title", classes="panel-title")
                        with VerticalScroll(id="day-details-scroll"):
                            yield Static(id="day-details")
                        yield Static(id="day-scroll-hint")
            with Horizontal(classes="body-row bottom"):
                with Vertical(classes="left-column"):
                    with Vertical(id="commits-panel", classes="panel"):
                        yield Static("Year Commits", classes="panel-title")
                        yield DataTable(id="commits-table")
                with Vertical(classes="right-column"):
                    with Vertical(id="languages-panel", classes="panel"):
                        yield Static("Languages / Files", classes="panel-title")
                        yield Static(id="language-summary")
                        yield Static(id="language-files")
        yield Footer()

    def on_mount(self) -> None:
        self.watch(self, "theme", self.on_theme_change, init=False)
        self.title = f"{self.context.project_name} - TUI"
        print(f"\033]0;{self.context.project_name} - Git Viewer\007", end="", flush=True)
        self.query_one("#commits-table", DataTable).cursor_type = "row"

        commits_table = self.query_one("#commits-table", DataTable)
        commits_table.show_horizontal_scrollbar = False
        commits_table.add_column("Hash", width=8)
        commits_table.add_column("Message", width=28)
        commits_table.add_column("Author", width=12)
        commits_table.add_column("When", width=7)
        commits_table.add_column("Files", width=5)

        self._apply_theme()
        self._refresh_year_options()
        self.refresh_view()

    def on_theme_change(self, old_value: str, new_value: str) -> None:
        self._apply_theme()
        self.refresh_view()

    def _apply_theme(self) -> None:
        palette = resolve_theme_palette(self.current_theme)
        self._theme_palette = palette

        summary_bar = self.query_one("#summary-bar", Static)
        summary_bar.styles.background = palette.surface
        summary_bar.styles.border = ("solid", palette.border)

        for panel in self.query(".panel"):
            panel.styles.background = palette.panel
            panel.styles.border = ("solid", palette.border)

        for title in self.query(".panel-title"):
            title.styles.color = palette.muted

        self.query_one("#day-scroll-hint", Static).styles.color = palette.muted

    def _refresh_year_options(self) -> None:
        branch_data = self.service.get_branch_data(self.selected_branch)
        commits = filter_commits(branch_data.commits, self.selected_range, None)
        years = sorted({commit.authored_date.year for commit in commits}) or branch_data.years
        if self.selected_year not in years and years:
            self.selected_year = max(years)

    def _filtered_branch_data(self) -> tuple[BranchData, list[CommitRecord]]:
        branch_data = self.service.get_branch_data(self.selected_branch)
        commits = filter_commits(branch_data.commits, self.selected_range, self.selected_year)
        return branch_data, commits

    def refresh_view(self) -> None:
        self._suspend_table_events = True
        branch_data, commits = self._filtered_branch_data()
        counts: collections.Counter[dt.date] = collections.Counter(
            commit.authored_date for commit in commits
        )

        if self.selected_day is None or self.selected_day not in counts:
            self.selected_day = max(counts) if counts else None
        if self.selected_commit_hash not in {commit.commit_hash for commit in commits}:
            day_commits = [
                commit for commit in commits if self.selected_day and commit.authored_date == self.selected_day
            ]
            self.selected_commit_hash = day_commits[0].commit_hash if day_commits else (commits[0].commit_hash if commits else None)

        self.query_one("#graph-title", Static).update(
            build_graph_title(
                self.selected_branch,
                self.selected_range,
                self.selected_year,
                self._theme_palette,
            )
        )
        self.query_one("#summary-content", Static).update(
            build_summary_text(
                self.context.project_name,
                self.selected_branch,
                self.selected_year,
                commits,
                self.context.language_stats,
                self._theme_palette,
            )
        )
        self._update_commits_table(commits)
        self._day_keys = sorted(counts.keys(), reverse=True)
        self._update_selection_views(commits, counts)
        self.query_one("#language-summary", Static).update(
            build_language_summary(
                self.context.language_stats,
                self.selected_language,
                self._theme_palette,
            )
        )
        self._update_language_files(commits)
        self._suspend_table_events = False

    def _update_selection_views(
        self,
        commits: list[CommitRecord],
        counts: collections.Counter[dt.date],
    ) -> None:
        self.query_one("#graph-content", Static).update(
            render_contribution_graph(
                counts,
                self.selected_year,
                self.selected_day,
                self._theme_palette,
            )
        )
        self._update_day_details(commits)

    def _set_selected_commit(
        self,
        commits: list[CommitRecord],
        commit_hash: str | None,
        *,
        sync_table: bool,
    ) -> None:
        self.selected_commit_hash = commit_hash
        selected = next(
            (commit for commit in commits if commit.commit_hash == self.selected_commit_hash),
            None,
        )
        if selected is not None:
            self.selected_day = selected.authored_date
        counts: collections.Counter[dt.date] = collections.Counter(
            commit.authored_date for commit in commits
        )
        if sync_table and self.selected_commit_hash:
            visible_hashes = [commit.commit_hash for commit in commits[:30]]
            if self.selected_commit_hash in visible_hashes:
                table = self.query_one("#commits-table", DataTable)
                row_index = visible_hashes.index(self.selected_commit_hash)
                self._suspend_table_events = True
                try:
                    table.move_cursor(row=row_index, animate=False, scroll=True)
                except Exception:
                    pass
                finally:
                    self._suspend_table_events = False
        self._update_selection_views(commits, counts)

    def _update_commits_table(self, commits: list[CommitRecord]) -> None:
        table = self.query_one("#commits-table", DataTable)
        table.scroll_home(immediate=True, x_axis=True, y_axis=True)
        table.clear(columns=False)
        now = dt.datetime.utcnow()
        for commit in commits[:30]:
            table.add_row(
                commit.commit_hash[:7],
                truncate_middle(commit.message, 28),
                truncate_middle(commit.author_name, 12),
                rel_time(commit.authored_at, now),
                str(len(commit.changed_files)),
                key=commit.commit_hash,
            )
        if self.selected_commit_hash in {commit.commit_hash for commit in commits[:30]}:
            try:
                row_index = [commit.commit_hash for commit in commits[:30]].index(self.selected_commit_hash)
                table.move_cursor(row=row_index, animate=False, scroll=True)
            except Exception:
                pass

    def _update_day_details(self, commits: list[CommitRecord]) -> None:
        palette = self._theme_palette
        details = Text()
        if self.selected_day is None:
            self.query_one("#details-title", Static).update("Commit Details")
            details.append(
                "No active day in current selection.",
                style=make_style(palette.muted, bold=True),
            )
            self.query_one("#day-details", Static).update(details)
            self.query_one("#day-scroll-hint", Static).update("")
            self.query_one("#day-details-scroll", VerticalScroll).scroll_home(animate=False)
            return

        day_commits = [commit for commit in commits if commit.authored_date == self.selected_day]
        selected_commit = next(
            (commit for commit in day_commits if commit.commit_hash == self.selected_commit_hash),
            day_commits[0] if day_commits else None,
        )
        if selected_commit is None:
            self.query_one("#details-title", Static).update("Commit Details")
            details.append("No commit selected.", style=make_style(palette.muted))
            self.query_one("#day-details", Static).update(details)
            self.query_one("#day-scroll-hint", Static).update("")
            self.query_one("#day-details-scroll", VerticalScroll).scroll_home(animate=False)
            return

        title_row = Table.grid(expand=True)
        title_row.add_column(ratio=1)
        title_row.add_column(justify="right", width=7)
        title_row.add_row(
            Text("Commit Details", style=make_style(palette.muted, bold=True)),
            Text(selected_commit.commit_hash[:7], style=make_style(palette.muted)),
        )
        self.query_one("#details-title", Static).update(title_row)

        details.append(selected_commit.authored_date.isoformat(), style=make_style(palette.muted))
        details.append("  ", style=make_style(palette.muted))
        details.append(
            truncate_middle(selected_commit.author_name, 20),
            style=make_style(palette.foreground, bold=True),
        )
        details.append("\n")
        details.append(f"{selected_commit.message}\n", style=make_style(palette.foreground))
        details.append("\nFiles changed\n", style=make_style(palette.muted, bold=True))
        for index, path in enumerate(selected_commit.changed_files):
            suffix = "\n" if index < len(selected_commit.changed_files) - 1 else ""
            details.append(f"  - {path}{suffix}", style=make_style(palette.muted))
        self.query_one("#day-details", Static).update(details)
        overflow_hint = "Scroll for more" if len(selected_commit.changed_files) > 4 or len(selected_commit.message) > 54 else ""
        self.query_one("#day-scroll-hint", Static).update(overflow_hint)
        self.query_one("#day-details-scroll", VerticalScroll).scroll_home(animate=False)

    def _update_language_files(self, commits: list[CommitRecord]) -> None:
        palette = self._theme_palette
        activity = aggregate_file_activity(commits)
        files = self.context.files_by_language.get(self.selected_language, [])
        now = dt.datetime.utcnow()

        lines = Text()
        lines.append(self.selected_language, style=make_style(palette.foreground, bold=True))
        lines.append(f"  {len(files)} files\n", style=make_style(palette.muted))
        for path in files[:10]:
            info = activity.get(path)
            if info:
                top_author = info["authors"].most_common(1)[0][0] if info["authors"] else "-"
                lines.append(
                    f"{truncate_middle(path, 24)}\n",
                    style=make_style(palette.foreground),
                )
                lines.append(
                    f"  {info['commits']} commits  {truncate_middle(top_author, 10)}  {rel_time(info['last_modified'], now)}\n",
                    style=make_style(palette.muted),
                )
            else:
                lines.append(
                    f"{truncate_middle(path, 24)}\n",
                    style=make_style(palette.foreground),
                )
                lines.append(
                    "  no activity in selection\n",
                    style=make_style(palette.muted),
                )
        if len(files) > 10:
            lines.append(f"... {len(files) - 10} more files", style=make_style(palette.muted))
        self.query_one("#language-files", Static).update(lines)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if self._suspend_table_events:
            return
        if event.data_table.id == "commits-table":
            row_key = event.row_key.value if event.row_key else None
            if row_key:
                _branch_data, commits = self._filtered_branch_data()
                self._set_selected_commit(commits, str(row_key), sync_table=False)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if self._suspend_table_events:
            return
        if event.data_table.id == "commits-table":
            row_key = event.row_key.value if event.row_key else None
            if row_key and str(row_key) != self.selected_commit_hash:
                _branch_data, commits = self._filtered_branch_data()
                self._set_selected_commit(commits, str(row_key), sync_table=False)

    def action_prev_year(self) -> None:
        branch_data = self.service.get_branch_data(self.selected_branch)
        years = sorted(branch_data.years)
        if self.selected_year in years:
            idx = years.index(self.selected_year)
            if idx > 0:
                self.selected_year = years[idx - 1]
                self.refresh_view()

    def action_next_year(self) -> None:
        branch_data = self.service.get_branch_data(self.selected_branch)
        years = sorted(branch_data.years)
        if self.selected_year in years:
            idx = years.index(self.selected_year)
            if idx < len(years) - 1:
                self.selected_year = years[idx + 1]
                self.refresh_view()

    def action_cycle_range(self) -> None:
        values = [value for _label, value in RANGE_PRESETS]
        idx = values.index(self.selected_range)
        self.selected_range = values[(idx + 1) % len(values)]
        self._refresh_year_options()
        self.refresh_view()

    def action_open_branch_picker(self) -> None:
        self.push_screen(
            BranchPickerScreen(self.context.branches, self.selected_branch),
            self._handle_branch_selected,
        )

    def _handle_branch_selected(self, branch: str | None) -> None:
        if not branch or branch == self.selected_branch:
            return
        self.selected_branch = branch
        self.selected_day = None
        self.selected_commit_hash = None
        self._refresh_year_options()
        self.refresh_view()

    def action_day_down(self) -> None:
        if not self._day_keys:
            return
        if self.selected_day not in self._day_keys:
            next_day = self._day_keys[0]
        else:
            idx = self._day_keys.index(self.selected_day)
            if idx < len(self._day_keys) - 1:
                next_day = self._day_keys[idx + 1]
            else:
                return
        _branch_data, commits = self._filtered_branch_data()
        day_commits = [commit for commit in commits if commit.authored_date == next_day]
        self._set_selected_commit(
            commits,
            day_commits[0].commit_hash if day_commits else None,
            sync_table=True,
        )

    def action_day_up(self) -> None:
        if not self._day_keys:
            return
        if self.selected_day not in self._day_keys:
            next_day = self._day_keys[0]
        else:
            idx = self._day_keys.index(self.selected_day)
            if idx > 0:
                next_day = self._day_keys[idx - 1]
            else:
                return
        _branch_data, commits = self._filtered_branch_data()
        day_commits = [commit for commit in commits if commit.authored_date == next_day]
        self._set_selected_commit(
            commits,
            day_commits[0].commit_hash if day_commits else None,
            sync_table=True,
        )

    def action_next_language(self) -> None:
        if not self._language_keys:
            self._language_keys = [item["language"] for item in self.context.language_stats]
        if not self._language_keys:
            return
        idx = self._language_keys.index(self.selected_language)
        self.selected_language = self._language_keys[(idx + 1) % len(self._language_keys)]
        self.refresh_view()

    def action_prev_language(self) -> None:
        if not self._language_keys:
            self._language_keys = [item["language"] for item in self.context.language_stats]
        if not self._language_keys:
            return
        idx = self._language_keys.index(self.selected_language)
        self.selected_language = self._language_keys[(idx - 1) % len(self._language_keys)]
        self.refresh_view()


def main() -> None:
    args = parse_args()
    repo_dir = os.path.abspath(args.repo_dir)
    if not os.path.isdir(repo_dir):
        print(f"Error: directory does not exist: {repo_dir}")
        sys.exit(1)
    if not os.path.isdir(os.path.join(repo_dir, ".git")):
        print(f"Error: not a git repository (missing .git): {repo_dir}")
        sys.exit(1)

    exclude_languages = {s.strip() for s in args.exclude_languages.split(",") if s.strip()}
    ignore_dirs = {s.strip() for s in args.ignore_dirs.split(",") if s.strip()}

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

    GitViewerTUI(context, service).run()


if __name__ == "__main__":
    main()
