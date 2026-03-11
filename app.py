#!/usr/bin/env python3
"""Local git repository viewer with a GitHub-style contribution dashboard."""

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
from typing import Any

import numpy as np
import dash
from dash import Input, Output, State, ALL, dcc, html
import plotly.graph_objects as go


GITHUB_BG = "#0d1117"
GITHUB_PANEL = "#161b22"
GITHUB_BORDER = "#30363d"
GITHUB_TEXT = "#c9d1d9"
GITHUB_SUBTLE = "#8b949e"
GITHUB_LINK = "#58a6ff"

HEATMAP_COLORS = [
    [0.0, "#161b22"],
    [0.1, "#0e4429"],
    [0.3, "#006d32"],
    [0.6, "#26a641"],
    [1.0, "#39d353"],
]

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


@dataclass(frozen=True)
class CommitRecord:
    date: dt.date
    commit_hash: str
    message: str


@dataclass
class RepoData:
    repo_dir: str
    project_name: str
    commits: list[CommitRecord]
    years: list[int]
    language_mode: str
    language_stats: list[dict[str, Any]]
    files_by_language: dict[str, list[str]]


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


def load_commit_records(repo_dir: str) -> list[CommitRecord]:
    out = run_git(repo_dir, ["log", "--date=short", "--pretty=format:%H|%ad|%s"])
    records: list[CommitRecord] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue
        commit_hash, date_str, message = parts
        try:
            commit_date = dt.datetime.strptime(date_str.strip(), "%Y-%m-%d").date()
        except ValueError:
            continue
        records.append(CommitRecord(commit_date, commit_hash.strip(), message.strip()))
    return records


def scan_languages(
    repo_dir: str, exclude: set[str], mode: str
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    language_totals: dict[str, int] = {}
    files_by_language: dict[str, list[str]] = {}
    total = 0

    for root, dirs, files in os.walk(repo_dir):
        if ".git" in dirs:
            dirs.remove(".git")
        for filename in files:
            lang = detect_language(filename)
            if lang in exclude:
                continue

            full_path = os.path.join(root, filename)
            rel_path = os.path.relpath(full_path, repo_dir)
            files_by_language.setdefault(lang, []).append(rel_path)

            if mode == "files":
                value = 1
            else:
                try:
                    value = os.path.getsize(full_path)
                except OSError:
                    value = 0
            language_totals[lang] = language_totals.get(lang, 0) + value
            total += value

    stats: list[dict[str, Any]] = []
    for lang, value in language_totals.items():
        percent = (value / total * 100.0) if total else 0.0
        stats.append(
            {
                "language": lang,
                "value": value,
                "percentage": percent,
                "color": LANGUAGE_COLORS.get(lang, random_color(lang)),
            }
        )
        files_by_language[lang].sort()

    stats.sort(key=lambda item: item["percentage"], reverse=True)
    return stats, files_by_language


def split_commits_by_date_for_year(
    commits: list[CommitRecord], year: int
) -> tuple[collections.Counter[dt.date], dict[str, list[dict[str, str]]]]:
    counts: collections.Counter[dt.date] = collections.Counter()
    details: dict[str, list[dict[str, str]]] = {}
    for item in commits:
        if item.date.year != year:
            continue
        counts[item.date] += 1
        key = item.date.strftime("%Y-%m-%d")
        details.setdefault(key, []).append({"hash": item.commit_hash, "message": item.message})
    return counts, details


def build_year_grid(
    commit_counts: collections.Counter[dt.date], year: int
) -> tuple[np.ndarray, list[list[str]], int]:
    start = dt.date(year, 1, 1)
    end = dt.date(year, 12, 31)

    days_to_prev_sunday = (start.weekday() + 1) % 7
    first_sunday = start - dt.timedelta(days=days_to_prev_sunday)

    row_for_end = (end.weekday() + 1) % 7
    days_to_sat = 6 - row_for_end
    last_day = end + dt.timedelta(days=days_to_sat)

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
    return grid, customdata, num_weeks


def build_heatmap(grid: np.ndarray, customdata: list[list[str]], num_weeks: int) -> go.Figure:
    fig = go.Figure(
        data=go.Heatmap(
            z=grid,
            x=list(range(num_weeks)),
            y=list(range(7)),
            customdata=customdata,
            colorscale=HEATMAP_COLORS,
            showscale=False,
            hovertemplate="Date: %{customdata}<br>Commits: %{z}<extra></extra>",
        )
    )
    fig.update_layout(
        paper_bgcolor=GITHUB_BG,
        plot_bgcolor=GITHUB_BG,
        title=dict(text="GitHub-Style Contribution Graph", font=dict(color=GITHUB_TEXT)),
        margin=dict(l=20, r=20, t=40, b=20),
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(
            showgrid=False,
            zeroline=False,
            tickmode="array",
            tickvals=list(range(7)),
            ticktext=["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"],
            tickfont=dict(color=GITHUB_TEXT),
            autorange="reversed",
        ),
    )
    return fig


def make_language_panel(language_stats: list[dict[str, Any]], language_mode: str) -> html.Div:
    legend = html.Div(
        [
            html.Span(
                [
                    html.Span(
                        style={
                            "display": "inline-block",
                            "width": "12px",
                            "height": "12px",
                            "marginRight": "5px",
                            "backgroundColor": item["color"],
                        }
                    ),
                    html.Span(
                        f"{item['language']}: {format_percent(item['percentage'])}",
                        style={"marginRight": "10px"},
                    ),
                ],
                id={"type": "language-legend", "index": item["language"]},
                style={"cursor": "pointer", "display": "inline-block", "marginRight": "10px"},
            )
            for item in language_stats
        ],
        style={"marginTop": "5px", "fontSize": "12px", "color": GITHUB_TEXT},
    )

    bar = html.Div(
        [
            html.Div(
                style={
                    "backgroundColor": item["color"],
                    "width": f"{item['percentage']}%",
                    "height": "100%",
                }
            )
            for item in language_stats
        ],
        style={"display": "flex", "height": "12px", "borderRadius": "3px", "overflow": "hidden"},
    )

    mode_label = "bytes" if language_mode == "bytes" else "files"
    return html.Div(
        [
            html.Div(
                f"Languages by {mode_label}",
                style={"fontSize": "12px", "color": GITHUB_SUBTLE, "marginBottom": "6px"},
            ),
            bar,
            legend,
        ],
        style={"textAlign": "right", "padding": "20px"},
    )


def build_layout(data: RepoData) -> html.Div:
    header = html.Div(
        [
            html.Div(
                [
                    html.Img(
                        src="https://github.githubassets.com/images/modules/logos_page/GitHub-Mark.png",
                        style={"height": "60px", "marginRight": "10px"},
                    ),
                    html.Div(
                        [
                            html.H1("GitHub Contribution Graph", style={"margin": "0", "color": GITHUB_TEXT}),
                            html.Div(data.project_name, style={"fontSize": "18px", "color": GITHUB_SUBTLE}),
                        ],
                        style={"display": "inline-block", "verticalAlign": "middle"},
                    ),
                    html.Div(
                        [
                            html.Label("Select Year:", style={"marginRight": "10px"}),
                            dcc.Dropdown(
                                id="year-dropdown",
                                options=[{"label": str(y), "value": y} for y in data.years],
                                value=max(data.years) if data.years else None,
                                clearable=False,
                                style={"width": "150px", "backgroundColor": GITHUB_PANEL, "color": GITHUB_TEXT},
                            ),
                        ],
                        style={"paddingTop": "10px", "marginLeft": "20px"},
                    ),
                ],
                style={"flex": "1", "textAlign": "left"},
            ),
            html.Div(make_language_panel(data.language_stats, data.language_mode), style={"flex": "1"}),
        ],
        style={"display": "flex", "justifyContent": "space-between", "alignItems": "center"},
    )

    return html.Div(
        [
            header,
            html.Div(
                id="language-files",
                children="Click a language in the legend to list matching files.",
                style={
                    "margin": "20px",
                    "padding": "10px",
                    "backgroundColor": GITHUB_PANEL,
                    "border": f"1px solid {GITHUB_BORDER}",
                    "color": GITHUB_TEXT,
                },
            ),
            dcc.Graph(id="contrib-graph", style={"height": "300px"}),
            html.Div(
                id="commit-details",
                children="Click on a day cell to see commit details.",
                style={
                    "marginTop": "20px",
                    "fontFamily": "monospace",
                    "backgroundColor": GITHUB_PANEL,
                    "border": f"1px solid {GITHUB_BORDER}",
                    "padding": "10px",
                },
            ),
            dcc.Store(id="commit-data-store"),
        ],
        style={
            "backgroundColor": GITHUB_BG,
            "color": GITHUB_TEXT,
            "minHeight": "100vh",
            "fontFamily": "Segoe UI, sans-serif",
            "padding": "12px",
        },
    )


def register_callbacks(app: dash.Dash, data: RepoData) -> None:
    @lru_cache(maxsize=4096)
    def files_for_commit(commit_hash: str) -> tuple[str, ...]:
        out = run_git(data.repo_dir, ["diff-tree", "--no-commit-id", "--name-only", "-r", commit_hash])
        return tuple(line.strip() for line in out.splitlines() if line.strip())

    @app.callback(
        Output("contrib-graph", "figure"),
        Output("commit-data-store", "data"),
        Input("year-dropdown", "value"),
    )
    def update_graph(selected_year: int | None):
        if not selected_year:
            return dash.no_update, dash.no_update
        counts, details = split_commits_by_date_for_year(data.commits, int(selected_year))
        grid, customdata, num_weeks = build_year_grid(counts, int(selected_year))
        return build_heatmap(grid, customdata, num_weeks), details

    @app.callback(
        Output("commit-details", "children"),
        Input("contrib-graph", "clickData"),
        State("commit-data-store", "data"),
    )
    def show_day_details(click_data: dict[str, Any] | None, commit_map: dict[str, Any] | None):
        if not click_data or not commit_map:
            return "Click on a day cell to see commit details."
        try:
            date_key = click_data["points"][0]["customdata"]
        except (KeyError, IndexError, TypeError):
            return "Unable to read selected day."

        details = commit_map.get(date_key, [])
        if not details:
            return html.Div(f"{date_key}: No commits.", style={"color": GITHUB_TEXT})

        sections = []
        all_files: set[str] = set()
        for item in details:
            commit_hash = item["hash"]
            message = item["message"]
            changed = files_for_commit(commit_hash)
            all_files.update(changed)
            sections.append(
                html.Div(
                    [
                        html.Div(commit_hash[:7], style={"fontWeight": "bold", "color": GITHUB_LINK}),
                        html.Div(message, style={"marginBottom": "5px", "color": GITHUB_TEXT}),
                        html.Div("Files Modified:", style={"fontStyle": "italic", "color": GITHUB_TEXT}),
                        html.Ul(
                            [html.Li(path, style={"color": GITHUB_SUBTLE, "fontSize": "12px"}) for path in (changed or ("None",))],
                            style={"listStyleType": "none", "paddingLeft": "10px", "marginTop": "5px"},
                        ),
                    ],
                    style={"borderBottom": f"1px solid {GITHUB_BORDER}", "padding": "10px 0"},
                )
            )

        if all_files:
            sections.append(
                html.Div(
                    [
                        html.Div(
                            "Overall Files Modified on this day:",
                            style={"fontWeight": "bold", "color": GITHUB_TEXT, "marginTop": "10px"},
                        ),
                        html.Div(", ".join(sorted(all_files)), style={"color": GITHUB_TEXT, "fontSize": "12px"}),
                    ],
                    style={"paddingTop": "10px", "borderTop": f"1px solid {GITHUB_BORDER}"},
                )
            )

        return html.Div(
            [html.Div(f"Commit details for {date_key}:", style={"fontSize": "16px", "fontWeight": "bold"})] + sections,
            style={"padding": "10px"},
        )

    @app.callback(
        Output("language-files", "children"),
        Input({"type": "language-legend", "index": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def show_language_files(_clicks: list[int | None]):
        ctx = dash.callback_context
        if not ctx.triggered:
            return ""
        try:
            trigger = ctx.triggered[0]["prop_id"].split(".")[0]
            language = json.loads(trigger)["index"]
        except Exception:
            return "Unable to read selected language."

        files = data.files_by_language.get(language, [])
        if not files:
            return html.Div(f"No files detected for {language}.", style={"color": GITHUB_TEXT})
        return html.Div(
            [
                html.H3(f"Files for {language} ({len(files)}):", style={"color": GITHUB_TEXT}),
                html.Ul(
                    [html.Li(path, style={"color": GITHUB_SUBTLE, "fontSize": "12px"}) for path in files],
                    style={"maxHeight": "220px", "overflowY": "auto"},
                ),
            ],
            style={"padding": "10px", "border": f"1px solid {GITHUB_BORDER}", "margin": "0"},
        )


def build_repo_data(repo_dir: str, exclude: set[str], language_mode: str) -> RepoData:
    commits = load_commit_records(repo_dir)
    if not commits:
        raise RuntimeError("No commits found in repository.")
    years = sorted({item.date.year for item in commits})
    language_stats, files_by_language = scan_languages(repo_dir, exclude, language_mode)
    return RepoData(
        repo_dir=repo_dir,
        project_name=os.path.basename(os.path.abspath(repo_dir)),
        commits=commits,
        years=years,
        language_mode=language_mode,
        language_stats=language_stats,
        files_by_language=files_by_language,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a local GitHub-style contribution viewer for any git repository."
    )
    parser.add_argument("repo_dir", help="Path to the target local git repository")
    parser.add_argument("--exclude", default="", help="Comma-separated language names to exclude")
    parser.add_argument(
        "--language-mode",
        choices=("bytes", "files"),
        default="bytes",
        help="Language percentages by byte size (bytes) or file count (files)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host bind address")
    parser.add_argument("--port", type=int, default=8050, help="Port number")
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

    exclude = {item.strip() for item in args.exclude.split(",") if item.strip()}
    try:
        data = build_repo_data(repo_dir, exclude, args.language_mode)
    except RuntimeError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    app = dash.Dash(__name__)
    app.title = f"Git Viewer - {data.project_name}"
    app.layout = build_layout(data)
    register_callbacks(app, data)

    print(f"Repository: {data.project_name}")
    print(f"Language mode: {data.language_mode}")
    print(f"Serving at: http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
