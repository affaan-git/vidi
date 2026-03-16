"""Rich render helpers."""

from __future__ import annotations

import collections
import datetime as dt
from typing import Any

from rich.console import Group
from rich.table import Table
from rich.text import Text

from .data import HEATMAP_STYLES, RANGE_PRESETS, build_year_grid
from .models import LanguageStat, ThemePalette
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


def build_graph_title(
    branch: str,
    range_value: str,
    year: int,
    commit_count: int,
    palette: ThemePalette,
) -> Table:
    range_label = dict(RANGE_PRESETS).get(range_value, range_value)
    status = Text()
    status.append(branch, style=make_style(palette.foreground))
    status.append(" · ", style=make_style(palette.muted))
    status.append(range_label, style=make_style(palette.foreground))
    status.append(" · ", style=make_style(palette.muted))
    status.append(str(year), style=make_style(palette.foreground, bold=True))
    status.append(" · ", style=make_style(palette.muted))
    status.append(f"{commit_count} commits", style=make_style(palette.foreground))

    title = Table.grid(expand=True)
    title.add_column(ratio=1)
    title.add_column(justify="right")
    title.add_row(
        Text("Contribution Activity", style=make_style(palette.muted, bold=True)),
        status,
    )
    return title


def build_language_title(
    language_stats: list[LanguageStat],
    palette: ThemePalette,
) -> Text:
    title = Text()
    title.append("Languages", style=make_style(palette.muted, bold=True))
    title.append("  ", style=make_style(palette.muted))
    title.append(
        f"{len(language_stats)} languages",
        style=make_style(palette.muted),
    )
    if language_stats:
        top_language = language_stats[0]
        title.append("  ·  ", style=make_style(palette.muted))
        title.append("Top language ", style=make_style(palette.muted))
        title.append(
            str(top_language["language"]),
            style=make_style(palette.foreground, bold=True),
        )
        title.append(" ", style=make_style(palette.muted))
        title.append(
            f"{float(top_language['percentage']):.1f}%",
            style=make_style(palette.muted),
        )
    return title


def build_language_summary(
    language_stats: list[LanguageStat],
    selected_language: str,
    palette: ThemePalette,
    *,
    width: int,
    max_rows: int = 2,
) -> Group:
    if not language_stats:
        return Group(Text("No language data", style=make_style(palette.muted)))

    bar_width = max(8, width)
    total_percentage = sum(max(float(item["percentage"]), 0.0) for item in language_stats)
    if total_percentage <= 0:
        segment_widths = [0 for _item in language_stats]
        segment_widths[0] = bar_width
    else:
        raw_widths = [
            (max(float(item["percentage"]), 0.0) / total_percentage) * bar_width
            for item in language_stats
        ]
        segment_widths = [int(raw) for raw in raw_widths]
        remainder = bar_width - sum(segment_widths)
        if remainder > 0:
            order = sorted(
                range(len(raw_widths)),
                key=lambda index: raw_widths[index] - segment_widths[index],
                reverse=True,
            )
            for index in order[:remainder]:
                segment_widths[index] += 1

    bar = Text()
    for item, segment_width in zip(language_stats, segment_widths):
        if segment_width <= 0:
            continue
        bar.append("█" * segment_width, style=str(item["color"]))

    if not bar:
        bar.append("█" * bar_width, style=make_style(palette.surface))

    column_count = max(1, min(3, width // 24))
    visible_count = min(len(language_stats), column_count * max_rows)
    selected_index = next(
        (
            index
            for index, item in enumerate(language_stats)
            if item["language"] == selected_language
        ),
        0,
    )
    start = max(0, min(selected_index - (visible_count // 2), len(language_stats) - visible_count))
    visible_items = language_stats[start : start + visible_count]

    gap = 2
    cell_width = max(18, (width - gap * (column_count - 1)) // column_count)
    rows = (len(visible_items) + column_count - 1) // column_count
    legend_lines: list[Text] = []

    def build_cell(item: LanguageStat) -> Text:
        percentage_text = f"{float(item['percentage']):.1f}%"
        reserved = 2 + 2 + 1 + len(percentage_text)
        label_width = max(4, cell_width - reserved)
        cell = Text()
        is_selected = item["language"] == selected_language
        prefix = ">" if is_selected else " "
        cell.append(prefix + " ", style=make_style(palette.muted))
        cell.append("● ", style=str(item["color"]))
        label_style = (
            make_style(palette.foreground, bold=True)
            if is_selected
            else make_style(palette.foreground)
        )
        cell.append(
            truncate_middle(str(item["language"]), label_width),
            style=label_style,
        )
        cell.append(" ", style=make_style(palette.muted))
        cell.append(percentage_text, style=make_style(palette.muted))
        return cell

    for row_index in range(rows):
        line = Text()
        for column_index in range(column_count):
            item_index = row_index * column_count + column_index
            if item_index >= len(visible_items):
                continue
            cell = build_cell(visible_items[item_index])
            padding = max(0, cell_width - len(cell.plain))
            line.append(cell)
            if column_index < column_count - 1:
                line.append(" " * (padding + gap))
        legend_lines.append(line)

    hidden_count = len(language_stats) - len(visible_items)
    if hidden_count > 0:
        more_line = Text()
        more_line.append(
            f"... {hidden_count} more languages",
            style=make_style(palette.muted),
        )
        legend_lines.append(more_line)

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
