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
from .utils import truncate_middle


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
    *,
    width: int,
) -> Text:
    title = Text()
    title.append("Languages", style=make_style(palette.muted, bold=True))
    title.append(" ", style=make_style(palette.muted))
    title.append(
        str(len(language_stats)),
        style=make_style(palette.foreground, bold=True),
    )

    if not language_stats:
        return title

    top_language = language_stats[0]
    title.append(" · ", style=make_style(palette.muted))
    title.append("Top ", style=make_style(palette.muted))
    title.append(
        truncate_middle(
            str(top_language["language"]),
            max(8, min(18, width // 3)),
        ),
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
    max_legend_lines: int = 3,
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

    legend_lines: list[Text] = []

    def build_entry(item: LanguageStat) -> Text:
        percentage_text = f"{float(item['percentage']):.1f}%"
        label_width = max(6, min(16, width // 3))
        cell = Text()
        is_selected = item["language"] == selected_language
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

    entry_separator = 3
    current_line = Text()
    current_width = 0
    shown_count = 0

    for item in language_stats:
        entry = build_entry(item)
        entry_width = len(entry.plain)
        separator_width = entry_separator if current_width else 0
        would_overflow = current_width and (current_width + separator_width + entry_width > width)
        if would_overflow:
            legend_lines.append(current_line)
            if len(legend_lines) >= max_legend_lines:
                break
            current_line = Text()
            current_width = 0
            separator_width = 0

        if current_width:
            current_line.append(" " * entry_separator, style=make_style(palette.muted))
            current_width += entry_separator

        current_line.append(entry)
        current_width += entry_width
        shown_count += 1

    if current_line and len(legend_lines) < max_legend_lines:
        legend_lines.append(current_line)

    hidden_count = len(language_stats) - shown_count
    if hidden_count > 0:
        more_line = Text()
        more_line.append(
            f"... {hidden_count} more languages",
            style=make_style(palette.muted),
        )
        legend_lines.append(more_line)

    return Group(bar, *legend_lines)
