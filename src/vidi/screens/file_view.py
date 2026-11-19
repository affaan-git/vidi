"""Modal viewer for a single file's contents at a commit, with search."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, Static

from ..repository import RepoService
from ..text_view import TextView
from ..theme import make_style, resolve_theme_palette
from .shared import BACK_GROUP, JUMP_GROUP, MOVE_GROUP, PAGE_GROUP, SEARCH_GROUP


class FileViewScreen(ModalScreen[None]):
    """Show a file's contents as of a commit, with line numbers and / search."""

    FILE_GROUP = Binding.Group("File", compact=True)

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

    #file-view-content {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("down", "scroll_down", "Down", priority=True, group=MOVE_GROUP),
        Binding("up", "scroll_up", "Up", priority=True, group=MOVE_GROUP),
        Binding("j", "nav_down", "Down", priority=True, show=False),
        Binding("k", "nav_up", "Up", priority=True, show=False),
        Binding("left", "scroll_left", "Left", priority=True, show=False),
        Binding("right", "scroll_right", "Right", priority=True, show=False),
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
        self._query = ""

    @property
    def _path(self) -> str:
        return self._files[self._index]

    def compose(self) -> ComposeResult:
        with Vertical(id="file-view-box"):
            yield Static(id="file-view-title")
            yield Input(placeholder="Search in file", id="file-view-search")
            yield TextView(
                wrap=self.app.wrap_file_view, line_numbers=True, id="file-view-content"
            )
        yield Footer()

    def on_mount(self) -> None:
        self.styles.background = resolve_theme_palette(self.app.current_theme).overlay
        self._load_content()
        self._render_view()
        self._scroll().focus()

    # Content

    def _load_content(self) -> None:
        palette = resolve_theme_palette(self.app.current_theme)
        content = self._service.get_file_at_commit(self._commit_hash, self._path)
        self._binary = content is None
        view = self._scroll()
        view.set_match_styles(
            make_style(palette.selected_fg, background=palette.selected_bg),
            make_style(palette.selected_fg, background=palette.primary, bold=True),
        )
        if content is None:
            view.set_lines(
                ["Unable to show this file (it may be binary)."],
                make_style(palette.muted, bold=True),
            )
            view.search("")
        else:
            view.set_lines(
                content.splitlines() or [""],
                make_style(palette.foreground),
                gutter_style=make_style(palette.muted),
            )
            view.search(self._query)

    @property
    def _match_lines(self) -> list[int]:
        return self._scroll().matches

    @property
    def _current_match(self) -> int:
        return self._scroll().current_match

    def apply_wrap_settings(self) -> None:
        self._scroll().wrap = self.app.wrap_file_view

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

    # Search

    def _search_input(self) -> Input:
        return self.query_one("#file-view-search", Input)

    def _search_focused(self) -> bool:
        try:
            return self._search_input().has_focus
        except Exception:
            return False

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # While typing a search, j/k and left/right go to the input and up/down keep scrolling.
        if self._search_focused() and action in (
            "nav_down",
            "nav_up",
            "scroll_left",
            "scroll_right",
        ):
            return False
        # Match keys work only while searching or when there are matches.
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
        if not self._binary:
            self._scroll().search(self._query)
        self._render_view()
        # Jump to the first match as you type.
        self._scroll().scroll_to_current_match()

    def action_next_match(self) -> None:
        self._step_match(1)

    def action_prev_match(self) -> None:
        self._step_match(-1)

    def _step_match(self, delta: int) -> None:
        view = self._scroll()
        if not view.matches:
            return
        view.step_match(delta)
        self._render_view()
        view.scroll_to_current_match()

    def _close_search(self) -> None:
        # Hide the box but keep the matches and highlights for n / N.
        self._search_input().display = False
        self._scroll().focus()

    # File stepping

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

    # Scrolling

    def _scroll(self) -> TextView:
        return self.query_one("#file-view-content", TextView)

    def action_scroll_down(self) -> None:
        self._scroll().scroll_down(animate=False)

    def action_scroll_up(self) -> None:
        self._scroll().scroll_up(animate=False)

    # j / k get their own action, held back while the search box has focus.
    def action_nav_down(self) -> None:
        self.action_scroll_down()

    def action_nav_up(self) -> None:
        self.action_scroll_up()

    def action_scroll_left(self) -> None:
        self._scroll().scroll_left(animate=False)

    def action_scroll_right(self) -> None:
        self._scroll().scroll_right(animate=False)

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
