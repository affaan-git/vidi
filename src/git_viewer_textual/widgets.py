"""Custom widgets used by the app."""

from textual.widgets import Static


class FocusableStatic(Static):
    """A Static widget that can receive focus."""

    can_focus = True
