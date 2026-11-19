"""Binding groups and resize debouncing shared by the screens."""

from __future__ import annotations

from textual import events
from textual.binding import Binding
from textual.timer import Timer

MOVE_GROUP = Binding.Group("Move", compact=True)
PAGE_GROUP = Binding.Group("Page", compact=True)
JUMP_GROUP = Binding.Group("Jump", compact=True)
COMMIT_GROUP = Binding.Group("Commit", compact=True)
SEARCH_GROUP = Binding.Group("Search", compact=True)
BACK_GROUP = Binding.Group("Back", compact=True)

# Delay before re-rendering after a burst of resize events.
RESIZE_DEBOUNCE_SECONDS = 0.05


class DebouncedResize:
    """Screen mixin that calls `_render_after_resize` once a burst of resize events ends."""

    _resize_timer: Timer | None = None

    def on_resize(self, event: events.Resize) -> None:
        if not self.is_mounted:
            return
        if self._resize_timer is not None:
            self._resize_timer.stop()
        self._resize_timer = self.set_timer(RESIZE_DEBOUNCE_SECONDS, self._resize_settled)

    def _resize_settled(self) -> None:
        self._resize_timer = None
        if self.is_mounted:
            self._render_after_resize()
