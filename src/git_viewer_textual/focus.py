"""Shared focus helpers for scrollable widgets."""

from __future__ import annotations

from textual.containers import VerticalScroll
from textual.widget import Widget


def scroll_has_overflow(scroll: VerticalScroll) -> bool:
    """Return True when a scroll container has visible overflow."""

    return bool(scroll.max_scroll_y or scroll.max_scroll_x)


def set_scroll_focusability(*scrolls: VerticalScroll) -> None:
    """Make scroll containers focusable only when they can actually scroll."""

    for scroll in scrolls:
        scroll.can_focus = scroll_has_overflow(scroll)


def focus_targets(*widgets: Widget) -> tuple[Widget, ...]:
    """Return focus targets, skipping non-scrollable scroll containers."""

    return tuple(
        widget
        for widget in widgets
        if not isinstance(widget, VerticalScroll) or scroll_has_overflow(widget)
    )
