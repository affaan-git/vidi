"""Scrollable text view that renders only its visible rows, with wrap and search."""

from __future__ import annotations

from bisect import bisect_right
from itertools import accumulate
from typing import Sequence, Union

from rich.cells import cell_len, chop_cells, get_character_cell_size
from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual.geometry import Size
from textual.scroll_view import ScrollView
from textual.selection import Selection
from textual.strip import Strip

TAB_SIZE = 8

# A line is plain text drawn in its line style, or a Text carrying its own styles.
Line = Union[str, Text]


def _plain(line: Line) -> str:
    return line if isinstance(line, str) else line.plain


def _width(plain: str) -> int:
    return len(plain) if plain.isascii() else cell_len(plain)


def _row_count(plain: str, width: int) -> int:
    if plain.isascii():
        return (len(plain) - 1) // width + 1 if plain else 1
    return max(1, len(chop_cells(plain, width)))


def _offset_at_cell(plain: str, cells: int) -> tuple[int, int]:
    """The first character at or after a cell column, and the cells between the two."""
    width = 0
    for offset, char in enumerate(plain):
        if width >= cells:
            return offset, width - cells
        width += get_character_cell_size(char)
    return len(plain), max(0, width - cells)


def _tabbed_span(raw: str, start: int, end: int) -> str:
    """The characters of a tabbed line whose expanded columns start from start up to end."""
    chars: list[str] = []
    column = 0
    for char in raw:
        if column >= end:
            break
        if column >= start:
            chars.append(char)
        column = (column // TAB_SIZE + 1) * TAB_SIZE if char == "\t" else column + 1
    return "".join(chars)


def _row_starts(plain: str, width: int) -> list[int]:
    """Character offsets where each wrapped row of a line begins."""
    starts = [0]
    for row in chop_cells(plain, width)[:-1]:
        starts.append(starts[-1] + len(row))
    return starts


class TextView(ScrollView, can_focus=True):
    """Lines of text with an optional line-number gutter.

    In wrap mode long lines wrap at character boundaries. Otherwise lines keep
    their full width and the view scrolls sideways.
    """

    DEFAULT_CSS = """
    TextView {
        height: 1fr;
        scrollbar-gutter: stable;
    }

    TextView.-wrap {
        overflow-x: hidden;
    }
    """

    def __init__(
        self,
        *,
        wrap: bool = False,
        line_numbers: bool = False,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._wrap = wrap
        self.set_class(wrap, "-wrap")
        self._line_numbers = line_numbers
        self._lines: list[Line] = []
        self._plains: list[str] = []
        # The source text of each line whose tabs were expanded.
        self._tabbed: dict[int, str] = {}
        self._styles: Sequence[Style] | Style = Style()
        self._gutter_style = Style()
        self._match_style = Style(reverse=True)
        self._current_style = Style(reverse=True, bold=True)
        self._max_width = 0
        # Row index where each line starts in wrap mode, plus the total at the end.
        self._line_rows: list[int] | None = None
        self._layout_width = 0
        # Per-line caches for the rows on screen, cleared when content, width, or query change.
        self._starts_cache: dict[int, list[int]] = {}
        self._found_cache: dict[int, list[int]] = {}
        self._query = ""
        self._matches: list[int] = []
        self._match_set: set[int] = set()
        self._current = -1

    # Content

    def set_lines(
        self,
        lines: Sequence[Line],
        styles: Sequence[Style] | Style,
        *,
        gutter_style: Style | None = None,
    ) -> None:
        """Replace the content, keeping the scroll position and the active search."""
        if self.is_mounted and self.text_selection is not None:
            self.screen.clear_selection()
        self._tabbed = {
            index: line
            for index, line in enumerate(lines)
            if isinstance(line, str) and "\t" in line
        }
        self._lines = [
            line.expandtabs(TAB_SIZE) if isinstance(line, str) and "\t" in line else line
            for line in lines
        ]
        self._plains = [_plain(line) for line in self._lines]
        self._styles = styles
        if gutter_style is not None:
            self._gutter_style = gutter_style
        self._max_width = max((_width(plain) for plain in self._plains), default=0)
        self._line_rows = None
        self._found_cache.clear()
        query, self._query = self._query, ""
        self.search(query)
        self._update_layout()
        self.refresh()

    def set_match_styles(self, match: Style, current: Style) -> None:
        self._match_style = match
        self._current_style = current
        self.refresh()

    @property
    def lines(self) -> list[str]:
        """The plain text of each line."""
        return self._plains

    @property
    def wrap(self) -> bool:
        return self._wrap

    @wrap.setter
    def wrap(self, value: bool) -> None:
        if value == self._wrap:
            return
        top = self.line_at_row(int(self.scroll_y))
        self._wrap = value
        self.set_class(value, "-wrap")
        self._line_rows = None
        self._update_layout()
        self.scroll_to(x=0, y=self.row_for_line(top), animate=False)
        self.refresh()

    # Textual 6.2 counts a ScrollView as a container and disables selection in containers.
    @property
    def allow_select(self) -> bool:
        return True

    # Layout

    def _gutter_width(self) -> int:
        return len(str(len(self._lines))) + 2 if self._line_numbers else 0

    def _text_width(self) -> int:
        return max(1, self._layout_width - self._gutter_width())

    def _update_layout(self) -> None:
        """Measure rows for the current width and size the virtual canvas."""
        self._starts_cache.clear()
        width = self.scrollable_content_region.width
        if width > 0:
            self._layout_width = width
        if not self._wrap:
            self.virtual_size = Size(self._gutter_width() + self._max_width, len(self._lines))
            return
        if self._layout_width <= 0:
            return
        text_width = self._text_width()
        if self._max_width <= text_width:
            self._line_rows = None
            rows = len(self._lines)
        else:
            counts = (_row_count(plain, text_width) for plain in self._plains)
            self._line_rows = [0, *accumulate(counts)]
            rows = self._line_rows[-1]
        self.virtual_size = Size(self._layout_width, rows)

    def _ensure_layout(self) -> None:
        width = self.scrollable_content_region.width
        if width > 0 and width != self._layout_width:
            top = self.line_at_row(int(self.scroll_y))
            self._update_layout()
            if self._wrap:
                self.scroll_to(y=self.row_for_line(top), animate=False)

    def on_resize(self) -> None:
        self._ensure_layout()

    def row_for_line(self, line: int) -> int:
        """The first row of a line."""
        self._ensure_layout()
        line = max(0, min(line, len(self._lines)))
        if self._wrap and self._line_rows is not None:
            return self._line_rows[line]
        return line

    def line_at_row(self, row: int) -> int:
        """The line drawn on a row."""
        if self._wrap and self._line_rows is not None:
            line = bisect_right(self._line_rows, max(row, 0)) - 1
        else:
            line = row
        return max(0, min(line, len(self._lines) - 1))

    # Rendering

    def _line_style(self, index: int) -> Style:
        styles = self._styles
        return styles if isinstance(styles, Style) else styles[index]

    def _segment(self, index: int, start: int, end: int) -> Text:
        """Characters start to end of a line, styled and with search highlights."""
        line = self._lines[index]
        if isinstance(line, str):
            text = Text(line[start:end], style=self._line_style(index), no_wrap=True)
        else:
            text = line[start:end]
        if index in self._match_set:
            current = 0 <= self._current < len(self._matches) and (
                self._matches[self._current] == index
            )
            style = self._current_style if current else self._match_style
            size = len(self._query)
            for found in self._occurrences(index):
                if found + size > start and found < end:
                    text.stylize(style, max(found, start) - start, min(found + size, end) - start)
        selection = self.text_selection
        span = selection.get_span(index) if selection is not None else None
        if span is not None:
            first, last = span
            if last == -1:
                last = len(self._plains[index])
            if first < end and last > start:
                text.stylize(
                    self.screen.get_component_rich_style("screen--selection"),
                    max(first, start) - start,
                    min(last, end) - start,
                )
        return text

    def _occurrences(self, index: int) -> list[int]:
        cached = self._found_cache.get(index)
        if cached is not None:
            return cached
        lowered = self._plains[index].lower()
        found: list[int] = []
        start = lowered.find(self._query)
        while start >= 0 and self._query:
            found.append(start)
            start = lowered.find(self._query, start + len(self._query))
        if len(self._found_cache) > 1024:
            self._found_cache.clear()
        self._found_cache[index] = found
        return found

    def _starts(self, index: int, text_width: int) -> list[int]:
        """Character offsets where each wrapped row of a non-ASCII line begins."""
        starts = self._starts_cache.get(index)
        if starts is None:
            if len(self._starts_cache) > 1024:
                self._starts_cache.clear()
            starts = self._starts_cache[index] = _row_starts(self._plains[index], text_width)
        return starts

    def _row_span(self, index: int, part: int, text_width: int) -> tuple[int, int]:
        """The character span of one wrapped row of a line."""
        plain = self._plains[index]
        if plain.isascii():
            return part * text_width, (part + 1) * text_width
        starts = self._starts(index, text_width)
        part = min(part, len(starts) - 1)
        end = starts[part + 1] if part + 1 < len(starts) else len(plain)
        return starts[part], end

    def _part_of_offset(self, index: int, offset: int, text_width: int) -> int:
        """The wrapped row of a line that holds a character offset."""
        if self._plains[index].isascii():
            return offset // text_width
        return bisect_right(self._starts(index, text_width), offset) - 1

    def render_line(self, y: int) -> Strip:
        self._ensure_layout()
        width = self.scrollable_content_region.width or self.size.width
        base = self.rich_style
        row = int(self.scroll_y) + y
        index = self.line_at_row(row)
        if not self._lines or row >= self.virtual_size.height:
            return Strip.blank(width, base)
        text_width = max(1, width - self._gutter_width())
        console = self.app.console
        first_row = self.row_for_line(index)
        lead = 0
        if self._wrap:
            start, end = self._row_span(index, row - first_row, text_width)
        else:
            scroll_x = int(self.scroll_x)
            plain = self._plains[index]
            if plain.isascii():
                start, end = scroll_x, scroll_x + text_width
            else:
                start, lead = _offset_at_cell(plain, scroll_x)
                end = len(plain)
        text = self._segment(index, start, end)
        if lead:
            # The blank stands for the wide character cut off at the left edge.
            text = Text(" " * lead, style=self._line_style(index)) + text
        # Offsets map each cell back to the character it selects.
        body = (
            Strip(text.render(console))
            .crop(0, text_width)
            .extend_cell_length(text_width)
            .apply_offsets(start - lead, index)
        )
        if self._line_numbers:
            gutter = self._gutter_width()
            number = f"{index + 1:>{gutter - 2}}  " if row == first_row else " " * gutter
            # One segment per cell, each selecting from the start of the row.
            offset = Style.from_meta({"offset": (start if self._wrap else 0, index)})
            cell_style = self._gutter_style + offset
            gutter_strip = Strip([Segment(char, cell_style) for char in number], gutter)
            body = Strip.join([gutter_strip, body])
        return body.apply_style(base).adjust_cell_length(width, base)

    # Search

    @property
    def matches(self) -> list[int]:
        """Indices of the lines containing the query."""
        return self._matches

    @property
    def current_match(self) -> int:
        """Position of the current match in `matches`, or -1."""
        return self._current

    def search(self, query: str) -> None:
        """Find the lines containing the query, ignoring case."""
        query = query.lower()
        if query == self._query:
            return
        if self._query and query.startswith(self._query):
            candidates: Sequence[int] = self._matches
        else:
            candidates = range(len(self._plains))
        plains = self._plains
        self._matches = (
            [index for index in candidates if query in plains[index].lower()] if query else []
        )
        self._match_set = set(self._matches)
        self._found_cache.clear()
        self._query = query
        self._current = 0 if self._matches else -1
        self.refresh()

    def step_match(self, delta: int) -> None:
        if not self._matches:
            return
        self._current = (self._current + delta) % len(self._matches)
        self.refresh()

    def scroll_to_current_match(self) -> None:
        """Bring the current match into view with a little context above it."""
        if not 0 <= self._current < len(self._matches):
            return
        self._ensure_layout()
        index = self._matches[self._current]
        found = self._occurrences(index)
        start = found[0] if found else 0
        plain = self._plains[index]
        text_width = self._text_width()
        row = self.row_for_line(index)
        x: int | None = None
        if self._wrap:
            row += self._part_of_offset(index, start, text_width)
        else:
            first = _width(plain[:start])
            last = first + _width(plain[start : start + len(self._query)])
            left = int(self.scroll_x)
            margin = min(8, text_width // 4)
            if first < left:
                x = max(0, first - margin)
            elif last > left + text_width:
                x = max(0, last - text_width + margin)
        self.scroll_to(x=x, y=max(0, row - 2), animate=False)

    # Selection

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        """The selected text, with tabs as in the source."""
        start, end = selection
        first = 0 if start is None else start.y
        last = len(self._plains) - 1 if end is None else min(end.y, len(self._plains) - 1)
        parts: list[str] = []
        for index in range(first, last + 1):
            span = selection.get_span(index)
            if span is None:
                continue
            begin, finish = span
            plain = self._plains[index]
            if finish == -1:
                finish = len(plain)
            raw = self._tabbed.get(index)
            parts.append(plain[begin:finish] if raw is None else _tabbed_span(raw, begin, finish))
        return "\n".join(parts), "\n"
