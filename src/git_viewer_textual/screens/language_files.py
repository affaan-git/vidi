"""Modal listing the files that make up a selected language."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Footer, Static

from ..theme import make_style, resolve_theme_palette


class LanguageFilesScreen(ModalScreen[None]):
    """Show the file list for one language."""

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

    #language-files-scroll {
        height: auto;
        max-height: 22;
        scrollbar-gutter: stable;
    }
    """

    BINDINGS = [
        Binding("down", "scroll_down", "Down", priority=True, group=MOVE_GROUP),
        Binding("up", "scroll_up", "Up", priority=True, group=MOVE_GROUP),
        Binding("escape", "close", "Back", group=BACK_GROUP),
        Binding("q", "close", "Back", show=False, group=BACK_GROUP),
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
        yield Footer()

    def on_mount(self) -> None:
        palette = resolve_theme_palette(self.app.current_theme)
        # Frame color comes from the registered theme via CSS ($panel); only the
        # alpha-blended screen dim needs the palette here.
        self.styles.background = palette.overlay
        count = len(self._files)
        word = "file" if count == 1 else "files"

        title = Text()
        title.append(f"{self._language}  ", style=make_style(palette.foreground, bold=True))
        title.append(f"{count} {word} across the repository", style=make_style(palette.muted))
        self.query_one("#language-files-title", Static).update(title)

        body = Text()
        for index, path in enumerate(self._files):
            if index:
                body.append("\n")
            body.append(path, style=make_style(palette.foreground))
        self.query_one("#language-files-list", Static).update(body)

        scroll = self.query_one("#language-files-scroll", VerticalScroll)
        scroll.can_focus = True
        scroll.focus()

    def action_scroll_down(self) -> None:
        self.query_one("#language-files-scroll", VerticalScroll).scroll_down(animate=False)

    def action_scroll_up(self) -> None:
        self.query_one("#language-files-scroll", VerticalScroll).scroll_up(animate=False)

    def action_close(self) -> None:
        self.dismiss()
