"""Modal listing the files that make up a selected language."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from ..theme import make_style, resolve_theme_palette


class LanguageFilesScreen(ModalScreen[None]):
    """Show the file list for one language."""

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

    #language-files-scroll {
        height: auto;
        max-height: 22;
        scrollbar-gutter: stable;
    }

    #language-files-hint {
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close"),
    ]

    def __init__(self, language: str, files: list[str]) -> None:
        super().__init__()
        self._language = language
        self._files = files

    def compose(self) -> ComposeResult:
        with Vertical(id="language-files-box"):
            yield Static(id="language-files-title")
            with VerticalScroll(id="language-files-scroll"):
                yield Static(id="language-files-list")
            yield Static("Press Esc to close", id="language-files-hint")

    def on_mount(self) -> None:
        palette = resolve_theme_palette(self.app.current_theme)
        count = len(self._files)
        word = "file" if count == 1 else "files"

        title = Text()
        title.append(f"{self._language}  ", style=make_style(palette.foreground, bold=True))
        title.append(f"{count} {word}", style=make_style(palette.muted))
        self.query_one("#language-files-title", Static).update(title)

        body = Text()
        for index, path in enumerate(self._files):
            if index:
                body.append("\n")
            body.append(path, style=make_style(palette.foreground))
        self.query_one("#language-files-list", Static).update(body)
        self.query_one("#language-files-hint", Static).styles.color = palette.muted

    def action_close(self) -> None:
        self.dismiss()
