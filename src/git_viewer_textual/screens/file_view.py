"""Modal viewer for a single file's contents at a commit, with search."""

from __future__ import annotations

from rich.console import Console
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, Static

from ..models import ThemePalette
from ..repository import RepoService
from ..theme import make_style, resolve_theme_palette


class FileViewScreen(ModalScreen[None]):
    """Show a file's contents as of a commit, with line numbers and / search."""

    MOVE_GROUP = Binding.Group("Move", compact=True)
    PAGE_GROUP = Binding.Group("Page", compact=True)
    JUMP_GROUP = Binding.Group("Jump", compact=True)
    FILE_GROUP = Binding.Group("File", compact=True)
    SEARCH_GROUP = Binding.Group("Search", compact=True)
    BACK_GROUP = Binding.Group("Back", compact=True)

    CSS = """
    FileViewScreen {
        align: center middle;
    }

    #file-view-box {
        width: 90%;
        height: 90%;
        padding: 0 2 1 2;
        border: round $primary;
        background: $panel;
    }

    #file-view-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #file-view-search {
        display: none;
        margin-bottom: 1;
        border: solid $border;
        background: $surface;
        color: $text;
    }

    #file-view-search:focus {
        border: solid $primary;
    }

    #file-view-scroll {
        height: 1fr;
        scrollbar-gutter: stable;
    }

    #file-view-content {
        height: auto;
        min-height: 1;
    }
    """

    BINDINGS = [
        Binding("down", "scroll_down", "Down", priority=True, group=MOVE_GROUP),
        Binding("up", "scroll_up", "Up", priority=True, group=MOVE_GROUP),
        Binding("j", "nav_down", "Down", priority=True, show=False),
        Binding("k", "nav_up", "Up", priority=True, show=False),
        Binding("slash", "open_search", "Search"),
        Binding("enter", "next_match", "Next Match", priority=True),
        Binding("shift+enter", "prev_match", "Prev Match", priority=True),
        Binding("n", "next_match", "Next Match", group=SEARCH_GROUP),
        Binding("N", "prev_match", "Prev Match", group=SEARCH_GROUP),
        Binding("pageup", "page_up", "Page Up", priority=True, group=PAGE_GROUP),
        Binding("pagedown", "page_down", "Page Down", priority=True, group=PAGE_GROUP),
        Binding("home", "scroll_top", "Top", priority=True, group=JUMP_GROUP),
        Binding("end", "scroll_bottom", "Bottom", priority=True, group=JUMP_GROUP),
        Binding("[", "prev_file", "Prev File", group=FILE_GROUP),
        Binding("]", "next_file", "Next File", group=FILE_GROUP),
        Binding("escape", "close", "Back", group=BACK_GROUP),
        Binding("q", "close", "Back", show=False, group=BACK_GROUP),
    ]

    def __init__(
        self, service: RepoService, commit_hash: str, files: list[str], index: int
    ) -> None:
        super().__init__()
        self._service = service
        self._commit_hash = commit_hash
        self._files = files
        self._index = index
        self._binary = False
        self._lines: list[str] = []
        self._query = ""
        self._match_lines: list[int] = []
        self._current_match = -1
        self._row_offsets: list[int] | None = None
        self._offsets_width = 0

    @property
    def _path(self) -> str:
        return self._files[self._index]

    def compose(self) -> ComposeResult:
        with Vertical(id="file-view-box"):
            yield Static(id="file-view-title")
            yield Input(placeholder="Search in file", id="file-view-search")
            with VerticalScroll(id="file-view-scroll"):
                yield Static(id="file-view-content")
        yield Footer()

    def on_mount(self) -> None:
        self.styles.background = resolve_theme_palette(self.app.current_theme).overlay
        self._load_content()
        self._render_view()
        scroll = self._scroll()
        scroll.can_focus = True
        scroll.focus()

    # -- content -----------------------------------------------------------

    def _load_content(self) -> None:
        content = self._service.get_file_at_commit(self._commit_hash, self._path)
        self._binary = content is None
        self._lines = [] if content is None else (content.splitlines() or [""])
        self._row_offsets = None
        self._recompute_matches()

    def _render_view(self) -> None:
        palette = resolve_theme_palette(self.app.current_theme)
        title = Text()
        title.append(f"{self._path}  ", style=make_style(palette.foreground, bold=True))
        title.append(f"at {self._commit_hash[:7]}", style=make_style(palette.muted))
        if len(self._files) > 1:
            title.append(
                f"  ({self._index + 1}/{len(self._files)})",
                style=make_style(palette.muted),
            )
        if self._query:
            if self._match_lines:
                info = f"  {self._current_match + 1}/{len(self._match_lines)} matches"
                title.append(info, style=make_style(palette.primary))
            else:
                title.append("  no matches", style=make_style(palette.muted))
        self.query_one("#file-view-title", Static).update(title)
        self.query_one("#file-view-content", Static).update(self._build_content(palette))

    def _build_content(self, palette: ThemePalette) -> Text:
        text = Text()
        if self._binary:
            text.append(
                "Unable to show this file (it may be binary).",
                style=make_style(palette.muted, bold=True),
            )
            return text
        query = self._query.lower()
        current_line = (
            self._match_lines[self._current_match]
            if 0 <= self._current_match < len(self._match_lines)
            else -1
        )
        gutter = len(str(len(self._lines)))
        for index, line in enumerate(self._lines):
            if index > 0:
                text.append("\n")
            text.append(f"{index + 1:>{gutter}}  ", style=make_style(palette.muted))
            if query and query in line.lower():
                self._append_highlighted(text, line, query, palette, index == current_line)
            else:
                text.append(line, style=make_style(palette.foreground))
        return text

    def _append_highlighted(
        self, text: Text, line: str, query: str, palette: ThemePalette, current: bool
    ) -> None:
        plain = make_style(palette.foreground)
        match_style = (
            make_style(palette.selected_fg, background=palette.primary, bold=True)
            if current
            else make_style(palette.foreground, background=palette.selected_bg)
        )
        lowered = line.lower()
        start = 0
        while True:
            found = lowered.find(query, start)
            if found < 0:
                text.append(line[start:], style=plain)
                return
            text.append(line[start:found], style=plain)
            text.append(line[found : found + len(query)], style=match_style)
            start = found + len(query)

    # -- search ------------------------------------------------------------

    def _search_input(self) -> Input:
        return self.query_one("#file-view-search", Input)

    def _search_focused(self) -> bool:
        try:
            return self._search_input().has_focus
        except Exception:
            return False

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # While typing a search, only j/k need to reach the input (as text); the
        # arrows and page/jump keys keep scrolling so you can read around matches.
        if self._search_focused() and action in ("nav_down", "nav_up"):
            return False
        # Show/enable the match-nav (enter/shift+enter, n/N) only when there is a
        # search to step through, so the footer advertises it while typing.
        if action in ("next_match", "prev_match"):
            return bool(self._search_focused() or self._match_lines)
        return True

    def action_open_search(self) -> None:
        inp = self._search_input()
        inp.display = True
        inp.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "file-view-search":
            return
        self._query = event.value
        self._recompute_matches()
        self._render_view()
        # incremental: jump to the first match as you type
        if self._match_lines:
            self._scroll_to_current()

    def _recompute_matches(self) -> None:
        query = self._query.lower()
        if query and not self._binary:
            self._match_lines = [
                index for index, line in enumerate(self._lines) if query in line.lower()
            ]
        else:
            self._match_lines = []
        self._current_match = 0 if self._match_lines else -1

    def action_next_match(self) -> None:
        self._step_match(1)

    def action_prev_match(self) -> None:
        self._step_match(-1)

    def _step_match(self, delta: int) -> None:
        if not self._match_lines:
            return
        self._current_match = (self._current_match + delta) % len(self._match_lines)
        self._render_view()
        self._scroll_to_current()

    def _ensure_offsets(self) -> None:
        scroll = self._scroll()
        width = max(1, scroll.content_region.width or scroll.size.width or 80)
        if self._row_offsets is not None and self._offsets_width == width:
            return
        console = Console(width=width)
        gutter = len(str(len(self._lines)))
        offsets: list[int] = []
        row = 0
        for index, line in enumerate(self._lines):
            offsets.append(row)
            wrapped = Text(f"{index + 1:>{gutter}}  {line}").wrap(console, width)
            row += max(1, len(wrapped))
        self._row_offsets = offsets
        self._offsets_width = width

    def _scroll_to_current(self) -> None:
        if not 0 <= self._current_match < len(self._match_lines):
            return
        self._ensure_offsets()
        line = self._match_lines[self._current_match]
        assert self._row_offsets is not None
        target = self._row_offsets[line] if line < len(self._row_offsets) else 0
        # keep a little context above the match
        self._scroll().scroll_to(y=max(0, target - 2), animate=False)

    def _close_search(self) -> None:
        # Hide the box but keep the matches + highlights, so n / N still step
        # through them on the content (Esc again then closes the screen).
        self._search_input().display = False
        self._scroll().focus()

    # -- file stepping -----------------------------------------------------

    def action_prev_file(self) -> None:
        self._go_to_file(self._index - 1)

    def action_next_file(self) -> None:
        self._go_to_file(self._index + 1)

    def _go_to_file(self, index: int) -> None:
        if 0 <= index < len(self._files) and index != self._index:
            self._index = index
            self._load_content()  # re-applies the active search to the new file
            self._render_view()
            self._scroll().scroll_home(animate=False)

    # -- scrolling ---------------------------------------------------------

    def _scroll(self) -> VerticalScroll:
        return self.query_one("#file-view-scroll", VerticalScroll)

    def action_scroll_down(self) -> None:
        self._scroll().scroll_down(animate=False)

    def action_scroll_up(self) -> None:
        self._scroll().scroll_up(animate=False)

    # j / k -- separate action so it can yield to the search box while typing
    def action_nav_down(self) -> None:
        self.action_scroll_down()

    def action_nav_up(self) -> None:
        self.action_scroll_up()

    def action_page_down(self) -> None:
        self._scroll().scroll_page_down(animate=False)

    def action_page_up(self) -> None:
        self._scroll().scroll_page_up(animate=False)

    def action_scroll_top(self) -> None:
        self._scroll().scroll_home(animate=False)

    def action_scroll_bottom(self) -> None:
        self._scroll().scroll_end(animate=False)

    def action_close(self) -> None:
        # Esc closes an open search first, then the screen.
        if self._search_input().display:
            self._close_search()
            return
        self.dismiss()
