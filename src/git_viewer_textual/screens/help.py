"""Modal overlay documenting the keyboard shortcuts."""

from __future__ import annotations

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from ..theme import make_style, resolve_theme_palette

SHORTCUTS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Main screen",
        [
            ("j / k  or  up / down", "Move within the focused panel"),
            ("tab / shift+tab", "Switch between panels"),
            ("enter", "Open the commit, or the highlighted language's files"),
            ("h / l", "Select the older / newer active day"),
            ("[ / ]", "Step through years"),
            ("r", "Cycle the date range"),
            ("b", "Pick a branch"),
            ("?", "Show this help"),
            ("q", "Quit"),
        ],
    ),
    (
        "Commit detail",
        [
            ("j / k  or  up / down", "Move within the focused pane"),
            ("tab / shift+tab", "Switch between panes"),
            ("[ / ]", "Previous / next commit"),
            ("p", "Toggle file diff and full patch"),
            ("page up / page down", "Scroll by a page"),
            ("home / end", "Jump to the top or bottom"),
            ("esc / q", "Back to the main screen"),
        ],
    ),
    (
        "Branch picker",
        [
            ("type", "Filter the branch list"),
            ("up / down", "Move the highlight"),
            ("enter", "Switch to the highlighted branch"),
            ("esc / q", "Cancel"),
        ],
    ),
    (
        "Language files",
        [
            ("esc / q", "Back to the main screen"),
        ],
    ),
]


class HelpScreen(ModalScreen[None]):
    """Keyboard shortcut reference."""

    CSS = """
    HelpScreen {
        align: center middle;
    }

    #help-box {
        width: 76;
        max-width: 90%;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        border: round $primary;
        background: $panel;
    }

    #help-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #help-hint {
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close"),
        Binding("question_mark", "close", "Close"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="help-box"):
            yield Static("Keyboard shortcuts", id="help-title")
            yield Static(self._build_body(), id="help-body")
            yield Static("Press ? or Esc to close", id="help-hint")

    def on_mount(self) -> None:
        palette = resolve_theme_palette(self.app.current_theme)
        self.query_one("#help-title", Static).styles.color = palette.foreground
        self.query_one("#help-hint", Static).styles.color = palette.muted

    def _build_body(self) -> Table:
        palette = resolve_theme_palette(self.app.current_theme)
        grid = Table.grid(padding=(0, 2))
        grid.add_column(justify="left", no_wrap=True)
        grid.add_column(justify="left")
        for index, (section, rows) in enumerate(SHORTCUTS):
            if index > 0:
                grid.add_row("", "")
            grid.add_row(Text(section, style=make_style(palette.primary, bold=True)), "")
            for keys, description in rows:
                grid.add_row(
                    Text(keys, style=make_style(palette.foreground, bold=True)),
                    Text(description, style=make_style(palette.muted)),
                )
        return grid

    def action_close(self) -> None:
        self.dismiss()
