"""Custom widgets used by the app."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widgets import Header, Static
from textual.widgets._header import HeaderClockSpace, HeaderIcon, HeaderTitle


class FocusableStatic(Static):
    """A Static widget that can receive focus."""

    can_focus = True


class HeaderStatus(HeaderClockSpace):
    """Header status area that mirrors the clock slot."""

    DEFAULT_CSS = """
    HeaderStatus {
        dock: right;
        width: auto;
        min-width: 16;
        padding: 0 1;
        background: $foreground-darken-1 5%;
        color: $foreground;
        text-opacity: 85%;
        content-align: center middle;
    }
    """

    status: reactive[str] = reactive("")

    def render(self) -> Text:
        return Text(self.status)


class StatusHeader(Header):
    """Header with a live status slot on the right instead of a clock."""

    status: reactive[str] = reactive("")

    def compose(self) -> ComposeResult:
        yield HeaderIcon().data_bind(Header.icon)
        yield HeaderTitle()
        yield HeaderStatus().data_bind(StatusHeader.status)
