"""Rich render helpers."""

from __future__ import annotations

import collections
import datetime as dt

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual.color import Color

from .data import RANGE_PRESETS, build_date_grid
from .models import LanguageStat, ThemePalette
from .theme import make_style


def resolve_heatmap_colors(palette: ThemePalette) -> tuple[str, list[str]]:
    is_dark = palette.background.brightness < 0.5
    inactive_style = palette.background.blend(
        palette.foreground,
        0.10 if is_dark else 0.14,
    ).hex6
    padding_style = palette.background.blend(
        palette.panel,
        0.50 if is_dark else 0.45,
    ).hex6
    blend_steps = (0.28, 0.48, 0.68, 0.84) if is_dark else (0.30, 0.50, 0.70, 0.86)
    active_styles = [
        Color.parse(inactive_style).blend(palette.success, step).hex6
        for step in blend_steps
    ]
    # heatmap_styles[0] is the inactive (zero-commit) color; [1:] ramp toward success.
    return padding_style, [inactive_style, *active_styles]


def render_contribution_graph(
    commit_counts: collections.Counter[dt.date],
    start_date: dt.date,
    end_date: dt.date,
    selected_day: dt.date | None,
    palette: ThemePalette,
) -> Group:
    grid, _months, first_sunday = build_date_grid(commit_counts, start_date, end_date)
    num_weeks = len(grid[0]) if grid else 0
    header_width = max(num_weeks, 3)
    month_line = [" "] * header_width
    last_end = -4

    is_full_year = (
        start_date.month == 1
        and start_date.day == 1
        and end_date.month == 12
        and end_date.day == 31
        and start_date.year == end_date.year
    )
    # Month labels sit on a fixed 365-day axis so columns line up the same way
    # every year. A leap day would push every later month one slot to the right,
    # so fold Feb 29 into Feb 28's offset instead of giving it its own.
    month_lengths = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    month_offsets = [0]
    for month_length in month_lengths:
        month_offsets.append(month_offsets[-1] + month_length)
    total_days = max(1, (end_date - start_date).days)

    def day_offset(day: dt.date) -> int:
        if day.month == 2 and day.day == 29:
            return month_offsets[2] - 1
        return month_offsets[day.month - 1] + day.day - 1

    month_cursor = dt.date(start_date.year, start_date.month, 1)
    while month_cursor <= end_date:
        next_month = (
            dt.date(month_cursor.year + 1, 1, 1)
            if month_cursor.month == 12
            else dt.date(month_cursor.year, month_cursor.month + 1, 1)
        )
        month_end = next_month - dt.timedelta(days=1)
        visible_start = max(month_cursor, start_date)
        visible_end = min(month_end, end_date)
        label = month_cursor.strftime("%b")
        if is_full_year:
            projected_start = round(
                (day_offset(visible_start) / (month_offsets[-1] - 1))
                * (header_width - 1)
            )
            projected_end = round(
                (day_offset(visible_end) / (month_offsets[-1] - 1))
                * (header_width - 1)
            )
        else:
            projected_start = round(
                ((visible_start - start_date).days / total_days) * (header_width - 1)
            )
            projected_end = round(
                ((visible_end - start_date).days / total_days) * (header_width - 1)
            )
        span_width = max(1, projected_end - projected_start + 1)
        if span_width >= len(label):
            index = projected_start + (span_width - len(label)) // 2
        else:
            index = projected_start
        index = max(0, min(index, header_width - len(label)))
        if index < last_end:
            index = last_end
        if index + len(label) > header_width:
            month_cursor = next_month
            continue
        for offset, char in enumerate(label):
            pos = index + offset
            if pos < len(month_line):
                month_line[pos] = char
        last_end = index + len(label) + 1
        month_cursor = next_month

    header = Text("    " + "".join(month_line), style=make_style(palette.muted))

    lines: list[Text] = [header]
    day_labels = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    max_value = max((value for row in grid for value in row if value is not None), default=0)
    padding_style, heatmap_styles = resolve_heatmap_colors(palette)
    cell_char = "■"

    for row_index, day_name in enumerate(day_labels):
        line = Text(f"{day_name:<3} ", style=make_style(palette.muted))
        for col_index, value in enumerate(grid[row_index]):
            if value is None:
                line.append(cell_char, style=padding_style)
                continue
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
                selected_style = Color.parse(style).blend(palette.foreground, 0.25).hex6
                line.append(cell_char, style=f"bold {selected_style}")
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
    title.append(" ", style=make_style(palette.muted))
    title.append(
        str(len(language_stats)),
        style=make_style(palette.foreground, bold=True),
    )
    return title


def build_language_option(item: LanguageStat, palette: ThemePalette) -> Text:
    """Render one selectable language row: a colored dot, the name, and share."""
    text = Text()
    text.append("● ", style=str(item["color"]))
    text.append(str(item["language"]), style=make_style(palette.foreground))
    text.append(f"  {float(item['percentage']):.1f}%", style=make_style(palette.muted))
    return text


def build_language_bar(
    language_stats: list[LanguageStat],
    palette: ThemePalette,
    *,
    width: int,
) -> Text:
    bar_width = max(8, width)
    if not language_stats:
        bar = Text()
        bar.append("█" * bar_width, style=make_style(palette.surface))
        return bar
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

    return bar
