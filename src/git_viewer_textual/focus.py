"""Shared focus helpers for scrollable widgets."""

from __future__ import annotations

from textual.containers import VerticalScroll
from textual.widget import Widget

_FOCUS_PENDING_ATTR = "_git_viewer_scroll_focus_pending"


def scroll_has_overflow(scroll: VerticalScroll) -> bool:
    """Return True when a scroll container has visible overflow."""

    return bool(scroll.max_scroll_y or scroll.max_scroll_x)


def mark_scroll_focus_pending(*scrolls: VerticalScroll) -> None:
    """Disable scroll focus while content/layout is being recomputed."""

    for scroll in scrolls:
        setattr(scroll, _FOCUS_PENDING_ATTR, True)
        scroll.can_focus = False


def scroll_is_focusable(scroll: VerticalScroll) -> bool:
    """Return True when a scroll container is measured and can scroll."""

    return not bool(getattr(scroll, _FOCUS_PENDING_ATTR, False)) and scroll_has_overflow(
        scroll
    )


def set_scroll_focusability(*scrolls: VerticalScroll) -> None:
    """Make scroll containers focusable only when they can actually scroll."""

    for scroll in scrolls:
        setattr(scroll, _FOCUS_PENDING_ATTR, False)
        scroll.can_focus = scroll_has_overflow(scroll)


def focus_targets(*widgets: Widget) -> tuple[Widget, ...]:
    """Return focus targets, skipping non-scrollable scroll containers."""

    return tuple(
        widget
        for widget in widgets
        if not isinstance(widget, VerticalScroll) or scroll_is_focusable(widget)
    )
