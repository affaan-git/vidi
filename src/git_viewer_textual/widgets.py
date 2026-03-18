"""Custom widgets used by the app."""

from __future__ import annotations

from datetime import datetime

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


class FocusableStatic(Static):
    """A Static widget that can receive focus."""

    can_focus = True


class TopBarIcon(Static):
    """Left icon slot for the top bar."""

    DEFAULT_CSS = """
    TopBarIcon {
        width: 100%;
        height: 1;
        padding: 0 1;
        content-align: left middle;
    }

    TopBarIcon:hover {
        background: $foreground 10%;
    }
    """

    icon: reactive[str] = reactive("⭘")

    def on_mount(self) -> None:
        if self.app.ENABLE_COMMAND_PALETTE:
            self.tooltip = "Open the command palette"
        else:
            self.disabled = True

    async def on_click(self, event: events.Click) -> None:
        event.stop()
        if self.app.ENABLE_COMMAND_PALETTE:
            await self.run_action("app.command_palette")

    def render(self) -> str:
        return self.icon


class TopBar(Widget):
    """Project-wide top bar with centered title and fixed side slots."""

    DEFAULT_CSS = """
    TopBar {
        dock: top;
        width: 100%;
        height: 1;
        background: $panel;
        color: $foreground;
    }

    .top-bar-row {
        width: 100%;
        height: 1;
    }

    .top-bar-side {
        width: 12;
        height: 1;
    }

    .top-bar-title {
        width: 1fr;
        height: 1;
        content-align: center middle;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }

    .top-bar-status {
        padding: 0 1;
        background: $foreground-darken-1 5%;
        color: $foreground;
        text-opacity: 85%;
        content-align: right middle;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    """

    status_text: reactive[str] = reactive("")
    side_width: reactive[int] = reactive(12)

    def __init__(
        self,
        *,
        show_clock: bool = False,
        side_width: int = 12,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        icon: str = "⭘",
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.show_clock = show_clock
        self.side_width = side_width
        self.icon = icon

    def compose(self) -> ComposeResult:
        with Horizontal(classes="top-bar-row"):
            yield TopBarIcon(self.icon, classes="top-bar-side")
            yield Static(classes="top-bar-title")
            yield Static(classes="top-bar-side top-bar-status")

    def on_mount(self) -> None:
        self.watch(self.app, "title", self._update_title, init=False)
        self.watch(self.app, "sub_title", self._update_title, init=False)
        self.watch(self.screen, "title", self._update_title, init=False)
        self.watch(self.screen, "sub_title", self._update_title, init=False)
        self._apply_side_width()
        self._update_title()
        if self.show_clock:
            self._tick_clock()
            self.set_interval(1, self._tick_clock)
        else:
            self._update_status()

    def watch_side_width(self, side_width: int) -> None:
        if self.is_mounted:
            self._apply_side_width()

    def watch_status_text(self, status_text: str) -> None:
        if self.is_mounted and not self.show_clock:
            self._update_status()

    def _apply_side_width(self) -> None:
        for widget in self.query(".top-bar-side"):
            widget.styles.width = self.side_width

    def _title_content(self) -> Text:
        app_title = self.app.title or ""
        app_sub_title = self.screen.sub_title if self.screen.sub_title is not None else self.app.sub_title
        title = self.screen.title if self.screen.title is not None else app_title
        subtitle = app_sub_title or ""

        text = Text()
        text.append(title, style="bold")
        if subtitle:
            text.append(" \u2013 ", style="dim")
            text.append(subtitle, style="dim")
        return text

    def _update_title(self, *args: object) -> None:
        if not self.is_mounted:
            return
        self.query_one(".top-bar-title", Static).update(self._title_content())

    def _update_status(self) -> None:
        if not self.is_mounted:
            return
        self.query_one(".top-bar-status", Static).update(self.status_text)

    def _tick_clock(self) -> None:
        if not self.is_mounted:
            return
        self.query_one(".top-bar-status", Static).update(datetime.now().strftime("%X"))
