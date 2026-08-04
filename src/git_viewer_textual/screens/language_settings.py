"""Modal for the language-scan settings, replacing the old CLI flags."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static

from ..theme import resolve_theme_palette


class LanguageSettingsScreen(ModalScreen[dict | None]):
    """Edit how languages are measured and what the scan skips.

    Dismisses with the new settings dict, or None if cancelled.
    """

    CSS = """
    LanguageSettingsScreen {
        align: center middle;
    }

    #language-settings-box {
        width: 64;
        max-width: 90%;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        border: round $primary;
        background: $panel;
    }

    #language-settings-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #language-settings-box Label {
        margin-top: 1;
        color: $text-muted;
    }

    #language-settings-box Select,
    #language-settings-box Input {
        margin-top: 0;
    }

    #language-settings-buttons {
        height: auto;
        margin-top: 1;
        align-horizontal: right;
    }

    #language-settings-buttons Button {
        margin-left: 2;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self, language_mode: str, exclude_languages: list[str], ignore_dirs: list[str]
    ) -> None:
        super().__init__()
        self._language_mode = language_mode
        self._exclude_languages = exclude_languages
        self._ignore_dirs = ignore_dirs

    def compose(self) -> ComposeResult:
        with Vertical(id="language-settings-box"):
            yield Static("Language settings", id="language-settings-title")
            yield Label("Measure by")
            yield Select(
                [("Bytes of code", "bytes"), ("Number of files", "files")],
                value=self._language_mode,
                allow_blank=False,
                id="language-mode",
            )
            yield Label("Exclude languages (comma separated)")
            yield Input(
                value=", ".join(self._exclude_languages),
                placeholder="e.g. JSON, YAML, Markdown",
                id="language-exclude",
            )
            yield Label("Also ignore directories (comma separated)")
            yield Input(
                value=", ".join(self._ignore_dirs),
                placeholder="e.g. vendor, generated",
                id="language-ignore",
            )
            with Horizontal(id="language-settings-buttons"):
                yield Button("Cancel", id="language-cancel")
                yield Button("Apply", variant="primary", id="language-apply")

    def on_mount(self) -> None:
        self.styles.background = resolve_theme_palette(self.app.current_theme).overlay

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_apply()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "language-apply":
            self.action_apply()
        else:
            self.action_cancel()

    @staticmethod
    def _split(value: str) -> list[str]:
        return [item.strip() for item in value.split(",") if item.strip()]

    def action_apply(self) -> None:
        self.dismiss(
            {
                "language_mode": self.query_one("#language-mode", Select).value,
                "exclude_languages": self._split(
                    self.query_one("#language-exclude", Input).value
                ),
                "ignore_dirs": self._split(self.query_one("#language-ignore", Input).value),
            }
        )

    def action_cancel(self) -> None:
        self.dismiss(None)
