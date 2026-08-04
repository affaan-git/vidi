"""Modal viewer for a single file's contents at a commit."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Footer, Static

from ..models import ThemePalette
from ..repository import RepoService
from ..theme import make_style, resolve_theme_palette


class FileViewScreen(ModalScreen[None]):
    """Show a file's contents as of a commit, with line numbers."""

    MOVE_GROUP = Binding.Group("Move", compact=True)
    PAGE_GROUP = Binding.Group("Page", compact=True)
    JUMP_GROUP = Binding.Group("Jump", compact=True)
    FILE_GROUP = Binding.Group("File", compact=True)
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
        Binding("j", "scroll_down", "Down", priority=True, show=False),
        Binding("k", "scroll_up", "Up", priority=True, show=False),
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

    @property
    def _path(self) -> str:
        return self._files[self._index]

    def compose(self) -> ComposeResult:
        with Vertical(id="file-view-box"):
            yield Static(id="file-view-title")
            with VerticalScroll(id="file-view-scroll"):
                yield Static(id="file-view-content")
        yield Footer()

    def on_mount(self) -> None:
        self.styles.background = resolve_theme_palette(self.app.current_theme).overlay
        self._render_view()
        scroll = self.query_one("#file-view-scroll", VerticalScroll)
        scroll.can_focus = True
        scroll.focus()

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
        self.query_one("#file-view-title", Static).update(title)
        self.query_one("#file-view-content", Static).update(self._render_file(palette))

    def action_prev_file(self) -> None:
        self._go_to_file(self._index - 1)

    def action_next_file(self) -> None:
        self._go_to_file(self._index + 1)

    def _go_to_file(self, index: int) -> None:
        if 0 <= index < len(self._files) and index != self._index:
            self._index = index
            self._render_view()
            self.query_one("#file-view-scroll", VerticalScroll).scroll_home(animate=False)

    def _render_file(self, palette: ThemePalette) -> Text:
        content = self._service.get_file_at_commit(self._commit_hash, self._path)
        text = Text()
        if content is None:
            text.append(
                "Unable to show this file (it may be binary).",
                style=make_style(palette.muted, bold=True),
            )
            return text
        lines = content.splitlines() or [""]
        gutter = len(str(len(lines)))
        for index, line in enumerate(lines, start=1):
            if index > 1:
                text.append("\n")
            text.append(f"{index:>{gutter}}  ", style=make_style(palette.muted))
            text.append(line, style=make_style(palette.foreground))
        return text

    def _scroll(self) -> VerticalScroll:
        return self.query_one("#file-view-scroll", VerticalScroll)

    def action_scroll_down(self) -> None:
        self._scroll().scroll_down(animate=False)

    def action_scroll_up(self) -> None:
        self._scroll().scroll_up(animate=False)

    def action_page_down(self) -> None:
        self._scroll().scroll_page_down(animate=False)

    def action_page_up(self) -> None:
        self._scroll().scroll_page_up(animate=False)

    def action_scroll_top(self) -> None:
        self._scroll().scroll_home(animate=False)

    def action_scroll_bottom(self) -> None:
        self._scroll().scroll_end(animate=False)

    def action_close(self) -> None:
        self.dismiss()
