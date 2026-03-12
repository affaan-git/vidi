#!/usr/bin/env python3
"""Local Git repository viewer with a GitHub-style read-only dashboard."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import dash
from dash import ALL, Input, Output, State, dcc, html
import plotly.graph_objects as go


# Theme constants exposed as CSS variables for HTML components.
BG = "var(--bg)"
PANEL = "var(--panel)"
BORDER = "var(--border)"
TEXT = "var(--text)"
SUBTLE = "var(--subtle)"
LINK = "var(--link)"
GOOD = "var(--good)"

THEME_CONFIG = {
    "dark": {
        "bg": "#0d1117",
        "panel": "#161b22",
        "border": "#30363d",
        "text": "#c9d1d9",
        "subtle": "#8b949e",
        "link": "#58a6ff",
        "good": "#3fb950",
        "heatmap": [
            [0.0, "#161b22"],
            [0.1, "#0e4429"],
            [0.3, "#006d32"],
            [0.6, "#26a641"],
            [1.0, "#39d353"],
        ],
    },
    "light": {
        "bg": "#f6f8fa",
        "panel": "#ffffff",
        "border": "#d0d7de",
        "text": "#24292f",
        "subtle": "#57606a",
        "link": "#0969da",
        "good": "#1f883d",
        "heatmap": [
            [0.0, "#ebedf0"],
            [0.1, "#9be9a8"],
            [0.3, "#40c463"],
            [0.6, "#30a14e"],
            [1.0, "#216e39"],
        ],
    },
}

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
    {"label": "All time", "value": "all"},
    {"label": "1 year", "value": "365"},
    {"label": "90 days", "value": "90"},
    {"label": "30 days", "value": "30"},
    {"label": "7 days", "value": "7"},
]


def run_git(repo_dir: str, args: list[str]) -> str:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=repo_dir, text=True, stderr=subprocess.PIPE
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else "unknown git error"
        raise RuntimeError(f"git {' '.join(args)} failed: {stderr}") from exc


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


def format_percent(percent: float) -> str:
    if 0 < percent < 0.1:
        return "<0.1%"
    return f"{percent:.1f}%"


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


def parse_iso_datetime(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return parsed


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
            changed = tuple(files_by_commit.get(commit_hash, []))
            record = CommitRecord(
                commit_hash=commit_hash,
                authored_at=authored_at,
                message=message.strip(),
                author_name=author_name.strip(),
                author_email=author_email.strip(),
                changed_files=changed,
            )
            commits.append(record)
            contributors[record.author_name] += 1

            for path in record.changed_files:
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
        return BranchData(
            branch=branch,
            commits=commits,
            years=years,
            contributors=contributors,
            file_activity=file_activity,
        )


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
        if not lines:
            continue
        for line in lines:
            if line.startswith("#"):
                continue
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
    for key in files_by_language:
        files_by_language[key].sort()
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
        repo_dir=repo_dir,
        language_mode=language_mode,
        exclude_languages=exclude_languages,
        ignore_dirs=ignore_dirs,
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
    service = RepoService(repo_dir, language_mode, exclude_languages, ignore_dirs)
    return context, service


def filter_commits(commits: list[CommitRecord], range_value: str, selected_year: int | None) -> list[CommitRecord]:
    filtered = commits

    if range_value != "all":
        days = int(range_value)
        if filtered:
            max_time = max(item.authored_at for item in filtered)
            cutoff = max_time - dt.timedelta(days=days)
            filtered = [item for item in filtered if item.authored_at >= cutoff]

    if selected_year is not None:
        filtered = [item for item in filtered if item.authored_date.year == selected_year]

    return filtered


def build_counts_and_details(commits: list[CommitRecord]) -> tuple[collections.Counter[dt.date], dict[str, list[dict[str, str]]]]:
    counts: collections.Counter[dt.date] = collections.Counter()
    details: dict[str, list[dict[str, str]]] = {}
    for item in commits:
        key = item.authored_date.strftime("%Y-%m-%d")
        counts[item.authored_date] += 1
        details.setdefault(key, []).append(
            {
                "hash": item.commit_hash,
                "message": item.message,
                "author": item.author_name,
                "email": item.author_email,
                "time": item.authored_at.isoformat(),
            }
        )
    return counts, details


def build_year_grid(
    commit_counts: collections.Counter[dt.date], year: int
) -> tuple[np.ndarray, list[list[str]], int, list[int], list[str]]:
    start = dt.date(year, 1, 1)
    end = dt.date(year, 12, 31)

    days_to_prev_sunday = (start.weekday() + 1) % 7
    first_sunday = start - dt.timedelta(days=days_to_prev_sunday)

    row_for_end = (end.weekday() + 1) % 7
    last_day = end + dt.timedelta(days=6 - row_for_end)

    total_days = (last_day - first_sunday).days + 1
    num_weeks = (total_days + 6) // 7

    grid = np.zeros((7, num_weeks), dtype=int)
    customdata = [["" for _ in range(num_weeks)] for _ in range(7)]

    for idx in range(total_days):
        day = first_sunday + dt.timedelta(days=idx)
        col = idx // 7
        row = idx % 7
        grid[row, col] = commit_counts.get(day, 0)
        customdata[row][col] = day.strftime("%Y-%m-%d")

    tickvals: list[int] = []
    ticktext: list[str] = []
    for month in range(1, 13):
        first_of_month = dt.date(year, month, 1)
        if first_of_month < first_sunday or first_of_month > last_day:
            continue
        col = (first_of_month - first_sunday).days // 7
        if col not in tickvals:
            tickvals.append(col)
            ticktext.append(first_of_month.strftime("%b"))

    return grid, customdata, num_weeks, tickvals, ticktext


def build_heatmap(
    grid: np.ndarray,
    customdata: list[list[str]],
    num_weeks: int,
    month_tickvals: list[int],
    month_ticktext: list[str],
    title: str,
    height: int,
    theme_name: str,
) -> go.Figure:
    palette = THEME_CONFIG.get(theme_name, THEME_CONFIG["dark"])
    max_val = max(int(grid.max()), 1)
    fig = go.Figure(
        data=go.Heatmap(
            z=grid,
            x=list(range(num_weeks)),
            y=list(range(7)),
            customdata=customdata,
            colorscale=palette["heatmap"],
            zmin=0,
            zmax=max_val,
            xgap=3,
            ygap=3,
            showscale=False,
            hovertemplate="Date: %{customdata}<br>Commits: %{z}<extra></extra>",
        )
    )
    fig.update_layout(
        paper_bgcolor=palette["bg"],
        plot_bgcolor=palette["bg"],
        margin=dict(l=16, r=16, t=42, b=16),
        title=dict(text=title, font=dict(color=palette["text"], size=18), x=0, xanchor="left"),
        height=height,
        dragmode="pan",
        xaxis=dict(
            showgrid=False,
            zeroline=False,
            tickmode="array",
            tickvals=month_tickvals,
            ticktext=month_ticktext,
            tickfont=dict(color=palette["subtle"]),
            side="top",
            range=[-0.5, num_weeks - 0.5],
            minallowed=-0.5,
            maxallowed=num_weeks - 0.5,
        ),
        yaxis=dict(
            showgrid=False,
            zeroline=False,
            tickmode="array",
            tickvals=list(range(7)),
            ticktext=["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
            tickfont=dict(color=palette["subtle"]),
            autorange="reversed",
            fixedrange=True,
        ),
    )
    return fig


def make_card(title: str, children: list[Any]) -> html.Div:
    return html.Div(
        [
            html.Div(title, style={"fontWeight": "600", "marginBottom": "10px", "color": TEXT}),
            *children,
        ],
        style={
            "backgroundColor": PANEL,
            "border": f"1px solid {BORDER}",
            "borderRadius": "8px",
            "padding": "12px",
            "marginBottom": "12px",
        },
    )


def top_repo_header(context: RepoContext) -> html.Div:
    return html.Div(
        [
            html.Div(
                [
                    html.Div("<>", style={"color": TEXT, "fontWeight": "700", "fontSize": "18px"}),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Span("local", style={"color": SUBTLE}),
                                    html.Span(" / ", style={"color": SUBTLE}),
                                    html.Span(context.project_name, style={"color": TEXT, "fontWeight": "700"}),
                                ],
                                style={"fontSize": "20px"},
                            ),
                            html.Div(
                                "Read-only local repository analytics",
                                style={"color": SUBTLE, "fontSize": "13px"},
                            ),
                        ]
                    ),
                ],
                style={"display": "flex", "alignItems": "center", "gap": "12px"},
            ),
            dcc.Tabs(
                id="top-tabs",
                value="overview",
                children=[
                    dcc.Tab(label="Overview", value="overview"),
                    dcc.Tab(label="Commits", value="commits"),
                    dcc.Tab(label="Activity", value="activity"),
                    dcc.Tab(label="Languages", value="languages"),
                    dcc.Tab(label="Files", value="files"),
                ],
                className="tabs-shell",
            ),
        ],
        style={
            "display": "flex",
            "justifyContent": "space-between",
            "alignItems": "center",
            "gap": "12px",
            "flexWrap": "wrap",
            "backgroundColor": PANEL,
            "border": f"1px solid {BORDER}",
            "borderRadius": "8px",
            "padding": "10px 14px",
            "marginBottom": "12px",
        },
    )


def controls_row(context: RepoContext) -> html.Div:
    return html.Div(
        [
            html.Div(
                [
                    html.Div("Branch", style={"fontSize": "12px", "color": SUBTLE, "marginBottom": "4px"}),
                    dcc.Dropdown(
                        id="branch-dropdown",
                        options=[{"label": b, "value": b} for b in context.branches],
                        value=context.current_branch,
                        clearable=False,
                        className="themed-dropdown",
                        style={"minWidth": "180px"},
                    ),
                ]
            ),
            html.Div(
                [
                    html.Div("Range", style={"fontSize": "12px", "color": SUBTLE, "marginBottom": "4px"}),
                    dcc.Dropdown(
                        id="range-dropdown",
                        options=RANGE_PRESETS,
                        value="all",
                        clearable=False,
                        className="themed-dropdown",
                        style={"minWidth": "140px"},
                    ),
                ]
            ),
            html.Div(
                [
                    html.Div("Year", style={"fontSize": "12px", "color": SUBTLE, "marginBottom": "4px"}),
                    dcc.Dropdown(
                        id="year-dropdown",
                        options=[],
                        value=None,
                        clearable=False,
                        className="themed-dropdown",
                        style={"minWidth": "120px"},
                    ),
                ]
            ),
            html.Div(
                [
                    html.Div("Theme", style={"fontSize": "12px", "color": SUBTLE, "marginBottom": "4px"}),
                    dcc.Dropdown(
                        id="theme-dropdown",
                        options=[
                            {"label": "Dark", "value": "dark"},
                            {"label": "Light", "value": "light"},
                        ],
                        value="dark",
                        clearable=False,
                        className="themed-dropdown",
                        style={"minWidth": "120px"},
                    ),
                ]
            ),
            html.Div(
                [
                    html.Button("Prev", id="year-prev", n_clicks=0, style={"marginRight": "6px"}),
                    html.Button("Next", id="year-next", n_clicks=0),
                ],
                style={"paddingTop": "20px"},
            ),
        ],
        style={
            "display": "flex",
            "gap": "12px",
            "alignItems": "flex-end",
            "flexWrap": "wrap",
            "backgroundColor": PANEL,
            "border": f"1px solid {BORDER}",
            "borderRadius": "8px",
            "padding": "10px",
            "marginBottom": "12px",
        },
    )


def language_legend(context: RepoContext, legend_type: str = "language-legend") -> html.Div:
    return html.Div(
        [
            html.Span(
                [
                    html.Span(
                        style={
                            "display": "inline-block",
                            "width": "10px",
                            "height": "10px",
                            "borderRadius": "50%",
                            "backgroundColor": item["color"],
                            "marginRight": "6px",
                        }
                    ),
                    f"{item['language']} {format_percent(item['percentage'])}",
                ],
                id={"type": legend_type, "index": item["language"]},
                style={"cursor": "pointer", "marginRight": "12px", "whiteSpace": "nowrap", "fontSize": "12px"},
            )
            for item in context.language_stats[:12]
        ],
        style={"display": "flex", "flexWrap": "wrap", "gap": "6px", "color": TEXT},
    )


def language_bar(context: RepoContext) -> html.Div:
    return html.Div(
        [
            html.Div(
                style={
                    "width": f"{item['percentage']}%",
                    "backgroundColor": item["color"],
                    "height": "100%",
                }
            )
            for item in context.language_stats
        ],
        style={"display": "flex", "height": "10px", "borderRadius": "999px", "overflow": "hidden"},
    )


def contribution_legend() -> html.Div:
    chips = ["var(--heat0)", "var(--heat1)", "var(--heat2)", "var(--heat3)", "var(--heat4)"]
    return html.Div(
        [
            html.Span("Less", style={"fontSize": "12px", "color": SUBTLE, "marginRight": "6px"}),
            *[
                html.Span(
                    style={
                        "display": "inline-block",
                        "width": "10px",
                        "height": "10px",
                        "borderRadius": "2px",
                        "backgroundColor": color,
                        "marginRight": "3px",
                    }
                )
                for color in chips
            ],
            html.Span("More", style={"fontSize": "12px", "color": SUBTLE, "marginLeft": "2px"}),
        ],
        style={"display": "flex", "justifyContent": "flex-end", "alignItems": "center", "marginTop": "6px"},
    )


def build_layout(context: RepoContext) -> html.Div:
    return html.Div(
        [
            top_repo_header(context),
            controls_row(context),
            html.Div(
                [
                    html.Div(
                        [
                            html.Div(
                                id="section-overview-graph",
                                children=[
                                    make_card(
                                        "Contribution Activity",
                                        [
                                            dcc.Graph(
                                                id="overview-graph",
                                                config={
                                                    "displaylogo": False,
                                                    "modeBarButtonsToRemove": [
                                                        "zoom2d",
                                                        "select2d",
                                                        "lasso2d",
                                                        "zoomIn2d",
                                                        "zoomOut2d",
                                                        "autoScale2d",
                                                        "resetScale2d",
                                                    ],
                                                    "scrollZoom": False,
                                                },
                                                style={"height": "250px"},
                                            ),
                                            contribution_legend(),
                                        ],
                                    ),
                                ],
                            ),
                            html.Div(
                                id="section-daily-details",
                                children=[
                                    make_card(
                                        "Daily Commit Details",
                                        [
                                            html.Details(
                                                open=False,
                                                children=[
                                                    html.Summary(
                                                        "Open day details",
                                                        style={"cursor": "pointer", "fontWeight": "600", "marginBottom": "8px"},
                                                    ),
                                                    html.Div(id="selected-day-summary", children="Click a day in the graph to inspect commits."),
                                                    dcc.Dropdown(
                                                        id="day-commit-dropdown",
                                                        options=[],
                                                        value=None,
                                                        clearable=True,
                                                        className="themed-dropdown",
                                                        placeholder="Select a commit from selected day",
                                                        style={"marginTop": "8px"},
                                                    ),
                                                    html.Div(id="commit-drawer", style={"marginTop": "10px"}),
                                                ],
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                            html.Div(
                                id="section-recent-commits",
                                children=[
                                    make_card(
                                        "Recent Commits",
                                        [
                                            html.Div(id="commit-table", className="mono", style={"fontSize": "12px", "overflowX": "auto"}),
                                        ],
                                    ),
                                ],
                            ),
                            html.Div(
                                id="section-language-explorer",
                                children=[
                                    make_card(
                                        "Language Explorer",
                                        [
                                            html.Div(f"Languages by {context.language_mode}", style={"color": SUBTLE, "fontSize": "12px", "marginBottom": "6px"}),
                                            language_bar(context),
                                            html.Div(style={"height": "8px"}),
                                            language_legend(context, legend_type="language-legend"),
                                            html.Div(id="selected-language-label", style={"marginTop": "8px", "fontSize": "12px", "color": SUBTLE}),
                                            html.Div(id="language-files", style={"marginTop": "10px"}),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                        id="main-column",
                        style={"minWidth": "0", "overflowY": "auto", "paddingRight": "6px"},
                    ),
                    html.Div(
                        [
                            html.Div(
                                id="section-about",
                                children=[make_card("About", [html.Div(context.about_text, style={"lineHeight": "1.5"})])],
                            ),
                            html.Div(
                                id="section-repo-stats",
                                children=[
                                    make_card(
                                        "Repository Stats",
                                        [
                                            html.Div(id="repo-stats"),
                                        ],
                                    )
                                ],
                            ),
                            html.Div(
                                id="section-sidebar-languages",
                                children=[
                                    make_card(
                                        "Languages",
                                        [
                                            language_bar(context),
                                            html.Div(style={"height": "8px"}),
                                            language_legend(context, legend_type="sidebar-language-legend"),
                                        ],
                                    )
                                ],
                            ),
                            html.Div(
                                id="section-contributors",
                                children=[
                                    make_card(
                                        "Top Contributors",
                                        [
                                            html.Div(id="contributors-list", className="mono", style={"fontSize": "12px"}),
                                        ],
                                    )
                                ],
                            ),
                            html.Div(
                                id="section-file-activity",
                                children=[
                                    make_card(
                                        "File Activity",
                                        [html.Div(id="file-activity-panel", className="mono", style={"fontSize": "12px"})],
                                    )
                                ],
                            ),
                        ],
                        id="sidebar-column",
                        style={"width": "320px", "minWidth": "280px", "overflowY": "auto", "paddingRight": "2px"},
                    ),
                ],
                id="main-grid",
                style={
                    "display": "grid",
                    "gridTemplateColumns": "minmax(0,1fr) 320px",
                    "gap": "12px",
                    "height": "calc(100vh - 170px)",
                    "minHeight": "0",
                },
            ),
            dcc.Store(id="day-details-store"),
            dcc.Store(id="current-day-store"),
            dcc.Store(id="selected-language-store"),
        ],
        id="app-root",
        className="theme-dark",
        style={
            "backgroundColor": BG,
            "color": TEXT,
            "height": "100vh",
            "overflow": "hidden",
            "padding": "12px",
            "fontFamily": "Segoe UI, -apple-system, BlinkMacSystemFont, sans-serif",
        },
    )


def render_commit_table(commits: list[CommitRecord], limit: int = 25) -> html.Table:
    now = dt.datetime.utcnow()
    rows = commits[:limit]
    return html.Table(
        [
            html.Thead(
                html.Tr(
                    [
                        html.Th("Hash", style={"textAlign": "left"}),
                        html.Th("Message", style={"textAlign": "left"}),
                        html.Th("Author", style={"textAlign": "left"}),
                        html.Th("When", style={"textAlign": "left"}),
                        html.Th("Files", style={"textAlign": "right"}),
                    ]
                )
            ),
            html.Tbody(
                [
                    html.Tr(
                        [
                            html.Td(c.commit_hash[:7], style={"color": LINK}),
                            html.Td(c.message[:90]),
                            html.Td(c.author_name),
                            html.Td(rel_time(c.authored_at, now), style={"color": SUBTLE}),
                            html.Td(str(len(c.changed_files)), style={"textAlign": "right", "color": SUBTLE}),
                        ],
                        style={"borderTop": f"1px solid {BORDER}"},
                    )
                    for c in rows
                ]
            ),
        ],
        style={"width": "100%", "borderCollapse": "collapse"},
    )


def calc_streaks(counts: collections.Counter[dt.date]) -> tuple[int, int]:
    if not counts:
        return 0, 0
    days = sorted(d for d, c in counts.items() if c > 0)
    longest = 0
    current = 0
    prev = None
    for day in days:
        if prev is None or (day - prev).days == 1:
            current += 1
        else:
            current = 1
        longest = max(longest, current)
        prev = day

    latest = days[-1]
    running = 0
    day = latest
    while counts.get(day, 0) > 0:
        running += 1
        day -= dt.timedelta(days=1)
    return running, longest


def build_file_activity_panel(file_activity: dict[str, dict[str, Any]], limit: int = 20) -> html.Div:
    rows = sorted(file_activity.items(), key=lambda kv: kv[1]["commits"], reverse=True)[:limit]
    if not rows:
        return html.Div("No file activity for this selection.", style={"color": SUBTLE})

    now = dt.datetime.utcnow()
    return html.Div(
        [
            html.Div(
                f"{path} | {info['commits']} commits | {rel_time(info['last_modified'], now)}",
                style={"padding": "4px 0", "borderTop": f"1px solid {BORDER}"},
            )
            for path, info in rows
        ]
    )


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


def register_callbacks(app: dash.Dash, context: RepoContext, service: RepoService) -> None:
    @app.callback(
        Output("app-root", "className"),
        Output("section-overview-graph", "style"),
        Output("section-daily-details", "style"),
        Output("section-recent-commits", "style"),
        Output("section-language-explorer", "style"),
        Output("section-about", "style"),
        Output("section-repo-stats", "style"),
        Output("section-sidebar-languages", "style"),
        Output("section-contributors", "style"),
        Output("section-file-activity", "style"),
        Input("theme-dropdown", "value"),
        Input("top-tabs", "value"),
    )
    def update_theme_and_tab_layout(theme: str, tab: str):
        base = {"display": "none"}
        sections = {
            "overview_graph": dict(base),
            "daily_details": dict(base),
            "recent_commits": dict(base),
            "language_explorer": dict(base),
            "about": dict(base),
            "repo_stats": dict(base),
            "sidebar_languages": dict(base),
            "contributors": dict(base),
            "file_activity": dict(base),
        }

        if tab == "overview":
            sections["overview_graph"] = {"display": "block"}
            sections["daily_details"] = {"display": "block"}
            sections["about"] = {"display": "block"}
            sections["repo_stats"] = {"display": "block"}
            sections["sidebar_languages"] = {"display": "block"}
        elif tab == "commits":
            sections["daily_details"] = {"display": "block"}
            sections["recent_commits"] = {"display": "block"}
            sections["contributors"] = {"display": "block"}
        elif tab == "activity":
            sections["overview_graph"] = {"display": "block"}
            sections["repo_stats"] = {"display": "block"}
            sections["file_activity"] = {"display": "block"}
        elif tab == "languages":
            sections["language_explorer"] = {"display": "block"}
            sections["sidebar_languages"] = {"display": "block"}
        elif tab == "files":
            sections["recent_commits"] = {"display": "block"}
            sections["file_activity"] = {"display": "block"}
            sections["language_explorer"] = {"display": "block"}
        else:
            sections["overview_graph"] = {"display": "block"}
            sections["daily_details"] = {"display": "block"}
            sections["about"] = {"display": "block"}
            sections["sidebar_languages"] = {"display": "block"}

        theme_class = "theme-light" if theme == "light" else "theme-dark"
        return (
            theme_class,
            sections["overview_graph"],
            sections["daily_details"],
            sections["recent_commits"],
            sections["language_explorer"],
            sections["about"],
            sections["repo_stats"],
            sections["sidebar_languages"],
            sections["contributors"],
            sections["file_activity"],
        )

    @app.callback(
        Output("selected-language-store", "data"),
        Output("top-tabs", "value", allow_duplicate=True),
        Input({"type": "language-legend", "index": ALL}, "n_clicks"),
        Input({"type": "sidebar-language-legend", "index": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def select_language(_main_clicks: list[int | None], _sidebar_clicks: list[int | None]):
        ctx = dash.callback_context
        if not ctx.triggered:
            return dash.no_update, dash.no_update
        trigger = ctx.triggered[0]["prop_id"].split(".")[0]
        try:
            payload = json.loads(trigger)
            language = payload["index"]
        except Exception:
            return dash.no_update, dash.no_update
        return language, "languages"

    @app.callback(
        Output("selected-language-label", "children"),
        Input("selected-language-store", "data"),
    )
    def show_selected_language(language: str | None):
        if not language:
            return "Click a language to inspect files and activity."
        return f"Selected language: {language}"

    @app.callback(
        Output("year-dropdown", "options"),
        Output("year-dropdown", "value"),
        Input("branch-dropdown", "value"),
        Input("range-dropdown", "value"),
        State("year-dropdown", "value"),
    )
    def update_year_dropdown(branch: str, range_value: str, current_year: int | None):
        data = service.get_branch_data(branch)
        commits = filter_commits(data.commits, range_value, None)
        years = sorted({c.authored_date.year for c in commits})
        if not years:
            years = data.years
        options = [{"label": str(y), "value": y} for y in years]
        if current_year in years:
            return options, current_year
        if not years:
            return options, None
        return options, max(years)

    @app.callback(
        Output("year-dropdown", "value", allow_duplicate=True),
        Input("year-prev", "n_clicks"),
        Input("year-next", "n_clicks"),
        State("year-dropdown", "options"),
        State("year-dropdown", "value"),
        prevent_initial_call=True,
    )
    def shift_year(prev_clicks: int, next_clicks: int, options: list[dict[str, Any]], year_value: int | None):
        ctx = dash.callback_context
        if not options or year_value is None or not ctx.triggered:
            return dash.no_update
        years = [int(item["value"]) for item in options]
        if year_value not in years:
            return dash.no_update
        idx = years.index(year_value)
        button_id = ctx.triggered[0]["prop_id"].split(".")[0]
        if button_id == "year-prev" and idx > 0:
            return years[idx - 1]
        if button_id == "year-next" and idx < len(years) - 1:
            return years[idx + 1]
        return year_value

    @app.callback(
        Output("overview-graph", "figure"),
        Output("day-details-store", "data"),
        Output("repo-stats", "children"),
        Output("contributors-list", "children"),
        Output("commit-table", "children"),
        Output("file-activity-panel", "children"),
        Input("branch-dropdown", "value"),
        Input("range-dropdown", "value"),
        Input("year-dropdown", "value"),
        Input("theme-dropdown", "value"),
    )
    def update_dashboard(branch: str, range_value: str, selected_year: int | None, theme: str):
        palette = THEME_CONFIG.get(theme, THEME_CONFIG["dark"])
        branch_data = service.get_branch_data(branch)
        base_commits = filter_commits(branch_data.commits, range_value, None)
        effective_year = selected_year if selected_year is not None else (max(branch_data.years) if branch_data.years else None)
        year_commits = filter_commits(base_commits, "all", effective_year)

        if effective_year is None:
            empty_fig = go.Figure()
            empty_fig.update_layout(paper_bgcolor=palette["bg"], plot_bgcolor=palette["bg"])
            return empty_fig, {}, "No commits", "No contributors", "", ""

        counts, details = build_counts_and_details(year_commits)
        grid, customdata, num_weeks, tickvals, ticktext = build_year_grid(counts, effective_year)

        overview_fig = build_heatmap(
            grid,
            customdata,
            num_weeks,
            tickvals,
            ticktext,
            title=f"Contribution Graph ({effective_year})",
            height=250,
            theme_name=theme,
        )
        total_commits = len(year_commits)
        active_days = len([v for v in counts.values() if v > 0])
        avg_per_day = (total_commits / active_days) if active_days else 0.0
        current_streak, longest_streak = calc_streaks(counts)

        now = dt.datetime.utcnow()
        latest_commit = year_commits[0].authored_at if year_commits else None
        latest_text = rel_time(latest_commit, now) if latest_commit else "n/a"

        stats = html.Div(
            [
                html.Div(f"Branch: {branch}", style={"padding": "2px 0"}),
                html.Div(f"Year: {effective_year}", style={"padding": "2px 0"}),
                html.Div(f"Commits: {total_commits}", style={"padding": "2px 0"}),
                html.Div(f"Active days: {active_days}", style={"padding": "2px 0"}),
                html.Div(f"Avg/day: {avg_per_day:.2f}", style={"padding": "2px 0"}),
                html.Div(f"Current streak: {current_streak}d", style={"padding": "2px 0"}),
                html.Div(f"Longest streak: {longest_streak}d", style={"padding": "2px 0"}),
                html.Div(f"Last commit: {latest_text}", style={"padding": "2px 0"}),
            ],
            className="mono",
            style={"fontSize": "12px", "color": TEXT},
        )

        contributor_counts = collections.Counter(c.author_name for c in year_commits)
        contributors = html.Div(
            [
                html.Div(
                    f"{name}: {count}",
                    style={"padding": "2px 0", "borderTop": f"1px solid {BORDER}"},
                )
                for name, count in contributor_counts.most_common(12)
            ]
            or [html.Div("No contributors", style={"color": SUBTLE})]
        )

        commit_table = render_commit_table(year_commits)
        file_panel = build_file_activity_panel(aggregate_file_activity(year_commits))

        return overview_fig, details, stats, contributors, commit_table, file_panel

    @app.callback(
        Output("selected-day-summary", "children"),
        Output("day-commit-dropdown", "options"),
        Output("day-commit-dropdown", "value"),
        Output("current-day-store", "data"),
        Input("overview-graph", "clickData"),
        State("day-details-store", "data"),
    )
    def select_day(click_data: dict[str, Any] | None, day_details: dict[str, Any] | None):
        if not click_data or not day_details:
            return "Click a day in the graph to inspect commits.", [], None, None

        try:
            day_key = click_data["points"][0]["customdata"]
        except (KeyError, IndexError, TypeError):
            return "Unable to parse selected day.", [], None, None

        commits = day_details.get(day_key, [])
        if not commits:
            return f"{day_key}: no commits", [], None, day_key

        options = [
            {
                "label": f"{item['hash'][:7]} {item['author']} - {item['message'][:70]}",
                "value": item["hash"],
            }
            for item in commits
        ]
        summary = f"{day_key}: {len(commits)} commit(s)"
        return summary, options, options[0]["value"], day_key

    @app.callback(
        Output("commit-drawer", "children"),
        Input("day-commit-dropdown", "value"),
        Input("branch-dropdown", "value"),
        State("current-day-store", "data"),
        State("day-details-store", "data"),
    )
    def show_commit_details(commit_hash: str | None, branch: str, day_key: str | None, day_details: dict[str, Any] | None):
        if not commit_hash or not day_key or not day_details:
            return html.Div("Pick a commit to view details.", style={"color": SUBTLE})

        candidates = day_details.get(day_key, [])
        selected = next((item for item in candidates if item["hash"] == commit_hash), None)
        if not selected:
            return html.Div("Selected commit not found.", style={"color": SUBTLE})

        branch_data = service.get_branch_data(branch)
        commit_obj = next((c for c in branch_data.commits if c.commit_hash == commit_hash), None)
        if not commit_obj:
            return html.Div("Commit data unavailable.", style={"color": SUBTLE})

        file_list = list(commit_obj.changed_files)
        return html.Div(
            [
                html.Div(commit_hash, className="mono", style={"color": LINK, "marginBottom": "4px"}),
                html.Div(selected["message"], style={"marginBottom": "6px"}),
                html.Div(f"Author: {selected['author']} ({selected['email']})", style={"fontSize": "12px", "color": SUBTLE}),
                html.Div(f"Changed files: {len(file_list)}", style={"fontSize": "12px", "color": SUBTLE, "marginTop": "6px"}),
                html.Ul(
                    [html.Li(path, className="mono", style={"fontSize": "12px", "color": TEXT}) for path in file_list[:200]]
                    or [html.Li("None", style={"color": SUBTLE})],
                    style={"maxHeight": "220px", "overflowY": "auto", "paddingLeft": "18px"},
                ),
            ]
        )

    @app.callback(
        Output("language-files", "children"),
        Input("selected-language-store", "data"),
        Input("branch-dropdown", "value"),
        Input("range-dropdown", "value"),
        Input("year-dropdown", "value"),
    )
    def show_language_files(language: str | None, branch: str, range_value: str, selected_year: int | None):
        if not language:
            return html.Div("Choose a language from the legend to view matching files.", style={"color": SUBTLE})

        files = context.files_by_language.get(language, [])
        if not files:
            return html.Div(f"No files found for {language}.", style={"color": SUBTLE})

        branch_data = service.get_branch_data(branch)
        filtered = filter_commits(branch_data.commits, range_value, selected_year)
        activity = aggregate_file_activity(filtered)

        rows = []
        now = dt.datetime.utcnow()
        for path in files:
            info = activity.get(path)
            if not info:
                continue
            top_author = info["authors"].most_common(1)[0][0] if info["authors"] else "-"
            rows.append(
                html.Tr(
                    [
                        html.Td(path, className="mono"),
                        html.Td(str(info["commits"]), style={"textAlign": "right"}),
                        html.Td(top_author),
                        html.Td(rel_time(info["last_modified"], now), style={"color": SUBTLE}),
                    ]
                )
            )

        if not rows:
            return html.Div(f"No activity in selection for {language} files.", style={"color": SUBTLE})

        return html.Div(
            [
                html.Div(
                    f"{language} files in current selection ({len(rows)})",
                    style={"marginBottom": "6px", "fontWeight": "600"},
                ),
                html.Table(
                    [
                        html.Thead(
                            html.Tr(
                                [
                                    html.Th("File", style={"textAlign": "left"}),
                                    html.Th("Commits", style={"textAlign": "right"}),
                                    html.Th("Top author", style={"textAlign": "left"}),
                                    html.Th("Last changed", style={"textAlign": "left"}),
                                ]
                            )
                        ),
                        html.Tbody(rows[:250]),
                    ],
                    style={"width": "100%", "fontSize": "12px", "borderCollapse": "collapse"},
                ),
            ]
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a local GitHub-style analytics dashboard for any git repository."
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
    parser.add_argument("--host", default="127.0.0.1", help="Host for Dash server")
    parser.add_argument("--port", type=int, default=8050, help="Port for Dash server")
    parser.add_argument("--debug", action="store_true", help="Enable Dash debug mode")
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

    exclude_languages = {s.strip() for s in args.exclude_languages.split(",") if s.strip()}
    ignore_dirs = {s.strip() for s in args.ignore_dirs.split(",") if s.strip()}

    try:
        context, service = build_repo_context(
            repo_dir=repo_dir,
            language_mode=args.language_mode,
            exclude_languages=exclude_languages,
            extra_ignored_dirs=ignore_dirs,
        )
        # Warm current branch cache for faster first render.
        service.get_branch_data(context.current_branch)
    except RuntimeError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    app = dash.Dash(__name__)
    app.title = f"Git Viewer - {context.project_name}"
    app.index_string = """
    <!DOCTYPE html>
    <html>
        <head>
            {%metas%}
            <title>{%title%}</title>
            {%favicon%}
            {%css%}
            <style>
                html, body {
                    margin: 0;
                    padding: 0;
                    background: var(--bg);
                    overflow: hidden;
                }
                .theme-dark {
                    --bg: #0d1117;
                    --panel: #161b22;
                    --border: #30363d;
                    --text: #c9d1d9;
                    --subtle: #8b949e;
                    --link: #58a6ff;
                    --good: #3fb950;
                    --heat0: #161b22;
                    --heat1: #0e4429;
                    --heat2: #006d32;
                    --heat3: #26a641;
                    --heat4: #39d353;
                    color-scheme: dark;
                }
                .theme-light {
                    --bg: #f6f8fa;
                    --panel: #ffffff;
                    --border: #d0d7de;
                    --text: #24292f;
                    --subtle: #57606a;
                    --link: #0969da;
                    --good: #1f883d;
                    --heat0: #ebedf0;
                    --heat1: #9be9a8;
                    --heat2: #40c463;
                    --heat3: #30a14e;
                    --heat4: #216e39;
                    color-scheme: light;
                }
                .Select-control, .Select-menu-outer, .Select-menu, .Select-value-label,
                .Select-placeholder, .Select-input > input {
                    background-color: var(--bg) !important;
                    color: var(--text) !important;
                    border-color: var(--border) !important;
                }
                .themed-dropdown > div,
                .themed-dropdown [class*="control"],
                .themed-dropdown [class*="menu"] {
                    background-color: var(--bg) !important;
                    border-color: var(--border) !important;
                    color: var(--text) !important;
                }
                .themed-dropdown [class*="singleValue"],
                .themed-dropdown [class*="placeholder"],
                .themed-dropdown [class*="option"],
                .themed-dropdown input {
                    color: var(--text) !important;
                }
                .theme-dark .themed-dropdown select,
                .theme-dark .themed-dropdown option {
                    background-color: #0d1117 !important;
                    color: #c9d1d9 !important;
                    border-color: #30363d !important;
                }
                .theme-light .themed-dropdown select,
                .theme-light .themed-dropdown option {
                    background-color: #f6f8fa !important;
                    color: #24292f !important;
                    border-color: #d0d7de !important;
                }
                .VirtualizedSelectOption { background-color: var(--bg) !important; color: var(--text) !important; }
                .VirtualizedSelectFocusedOption { background-color: var(--panel) !important; color: var(--text) !important; }
                button {
                    background: var(--panel);
                    color: var(--text);
                    border: 1px solid var(--border);
                    border-radius: 6px;
                    padding: 5px 10px;
                    cursor: pointer;
                }
                button:hover { filter: brightness(1.08); }
                .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, Liberation Mono, monospace; }
                .tabs-shell {
                    background: transparent !important;
                    border: none !important;
                }
                .tabs-shell .tab {
                    background: transparent !important;
                    border: none !important;
                    color: var(--subtle) !important;
                    padding: 6px 10px !important;
                }
                .tabs-shell .tab--selected {
                    color: var(--text) !important;
                    border-bottom: 2px solid var(--good) !important;
                }
            </style>
        </head>
        <body>
            {%app_entry%}
            <footer>
                {%config%}
                {%scripts%}
                {%renderer%}
            </footer>
        </body>
    </html>
    """
    app.layout = build_layout(context)
    register_callbacks(app, context, service)

    print(f"Repository: {context.project_name}")
    print(f"Current branch: {context.current_branch}")
    print(f"Serving at: http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
