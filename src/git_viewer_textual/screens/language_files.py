"""Modal listing a language's files in a commit's tree; open one to view it."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

from ..repository import RepoService
from ..theme import make_style, resolve_theme_palette
from .file_view import FileViewScreen


class LanguageFilesScreen(ModalScreen[None]):
    """Show one language's files at a commit; selecting a file opens its content."""

    MOVE_GROUP = Binding.Group("Move", compact=True)
    BACK_GROUP = Binding.Group("Back", compact=True)

    CSS = """
    LanguageFilesScreen {
        align: center middle;
    }

    #language-files-box {
        width: 72;
        max-width: 90%;
        height: auto;
        max-height: 80%;
        padding: 0 2 1 2;
        border: round $primary;
        background: $panel;
    }

    #language-files-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #language-files-list {
        height: auto;
        max-height: 22;
    }
    """

    BINDINGS = [
        Binding("down", "cursor_down", "Down", priority=True, group=MOVE_GROUP),
        Binding("up", "cursor_up", "Up", priority=True, group=MOVE_GROUP),
        Binding("enter", "open_file", "Open", priority=True),
        Binding("escape", "close", "Back", group=BACK_GROUP),
        Binding("q", "close", "Back", show=False, group=BACK_GROUP),
    ]

    def __init__(
        self, language: str, files: list[str], commit_hash: str, service: RepoService
    ) -> None:
        super().__init__()
        self._language = language
        self._files = files
        self._commit_hash = commit_hash
        self._service = service

    def compose(self) -> ComposeResult:
        with Vertical(id="language-files-box"):
            yield Static(id="language-files-title")
            yield OptionList(id="language-files-list")
        yield Footer()

    def on_mount(self) -> None:
        palette = resolve_theme_palette(self.app.current_theme)
        self.styles.background = palette.overlay
        count = len(self._files)
        word = "file" if count == 1 else "files"

        title = Text()
        title.append(f"{self._language}  ", style=make_style(palette.foreground, bold=True))
        title.append(
            f"{count} {word} in commit {self._commit_hash[:7]}",
            style=make_style(palette.muted),
        )
        self.query_one("#language-files-title", Static).update(title)

        options = self._options()
        options.add_options([Option(path, id=path) for path in self._files])
        if options.option_count:
            options.highlighted = 0
        options.focus()

    def _options(self) -> OptionList:
        return self.query_one("#language-files-list", OptionList)

    def action_cursor_down(self) -> None:
        self._options().action_cursor_down()

    def action_cursor_up(self) -> None:
        self._options().action_cursor_up()

    def action_open_file(self) -> None:
        options = self._options()
        if options.highlighted is not None:
            self._open(options.get_option_at_index(options.highlighted).id)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._open(event.option.id)

    def _open(self, path: str | None) -> None:
        if path:
            self.app.push_screen(FileViewScreen(self._service, self._commit_hash, path))

    def action_close(self) -> None:
        self.dismiss()
