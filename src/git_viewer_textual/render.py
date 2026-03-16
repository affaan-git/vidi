"""Rich render helpers."""

from __future__ import annotations

import collections
import datetime as dt
from typing import Any

from rich.console import Group
from rich.table import Table
from rich.text import Text

from .data import HEATMAP_STYLES, RANGE_PRESETS, build_year_grid
from .models import CommitRecord, LanguageStat, ThemePalette
from .theme import make_style
from .utils import rel_time, truncate_middle, utc_now


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
        palette.foreground,
        0.10 if palette.background.brightness < 0.5 else 0.06,
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


def build_summary_text(
    project_name: str,
    branch: str,
    year: int,
    commits: list[CommitRecord],
    language_stats: list[LanguageStat],
    palette: ThemePalette,
) -> Text:
    counts: collections.Counter[dt.date] = collections.Counter(
        commit.authored_date for commit in commits
    )
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


def build_graph_title(
    branch: str,
    range_value: str,
    year: int,
    palette: ThemePalette,
) -> Table:
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


def build_language_summary(
    language_stats: list[LanguageStat],
    selected_language: str,
    palette: ThemePalette,
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


def build_language_details(
    files: list[str],
    activity: dict[str, dict[str, Any]],
    selected_language: str,
    palette: ThemePalette,
) -> Text:
    now = utc_now()
    lines = Text()
    lines.append(selected_language, style=make_style(palette.foreground, bold=True))
    lines.append(f"  {len(files)} files\n", style=make_style(palette.muted))
    for path in files[:10]:
        info = activity.get(path)
        if info:
            top_author = info["authors"].most_common(1)[0][0] if info["authors"] else "-"
            lines.append(
                f"{truncate_middle(path, 28)}\n",
                style=make_style(palette.foreground),
            )
            lines.append(
                f"  {info['commits']} commits\n",
                style=make_style(palette.muted),
            )
            lines.append(
                f"  {truncate_middle(top_author, 14)}  {rel_time(info['last_modified'], now)}\n",
                style=make_style(palette.muted),
            )
        else:
            lines.append(
                f"{truncate_middle(path, 28)}\n",
                style=make_style(palette.foreground),
            )
            lines.append(
                "  no activity in selection\n",
                style=make_style(palette.muted),
            )
    if len(files) > 10:
        lines.append(
            f"... {len(files) - 10} more files",
            style=make_style(palette.muted),
        )
    return lines
