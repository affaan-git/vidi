"""Branch picker modal."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, OptionList, Static
from textual.widgets.option_list import Option

from ..theme import make_style, resolve_theme_palette
from ..utils import fuzzy_score, truncate_middle


class BranchPickerScreen(ModalScreen[str | None]):
    """Modal branch picker using Textual's option list widget."""

    BACK_GROUP = Binding.Group("Back", compact=True)
    MOVE_GROUP = Binding.Group("Move", compact=True)

    CSS = """
    BranchPickerScreen {
        align: center middle;
    }

    #branch-picker {
        width: 56;
        height: 20;
        padding: 1 1;
    }

    #branch-picker-title {
        text-style: bold;
        margin: 0 1 1 1;
    }

    #branch-picker-filter {
        margin: 0 1 1 1;
        border: solid $border-blurred;
        background: $surface;
        color: $text;
        outline: none;
    }

    #branch-picker-filter:focus {
        border: solid $primary;
        outline: none;
    }

    #branch-picker-options {
        margin: 0 1;
        border: solid $border-blurred;
        height: 1fr;
    }

    #branch-picker-options:focus {
        border: solid $border;
    }

    """

    BINDINGS = [
        Binding("down", "cursor_down", "Down", priority=True, group=MOVE_GROUP),
        Binding("up", "cursor_up", "Up", priority=True, group=MOVE_GROUP),
        Binding("enter", "choose_branch", "Choose", priority=True),
        Binding("escape", "cancel", "Back", group=BACK_GROUP),
        Binding("q", "cancel", "Back", show=False, group=BACK_GROUP),
    ]

    def __init__(self, branches: list[str], current_branch: str) -> None:
        super().__init__()
        self.branches = branches
        self.current_branch = current_branch
        self.filtered_branches = branches[:]
        self.highlighted_branch = current_branch if current_branch in branches else (
            branches[0] if branches else ""
        )

    def compose(self) -> ComposeResult:
        with Static(id="branch-picker"):
            yield Static("Select Branch", id="branch-picker-title")
            yield Input(placeholder="Filter branches", id="branch-picker-filter")
            yield OptionList(id="branch-picker-options")
        yield Footer()

    def on_mount(self) -> None:
        self.watch(self.app, "theme", self._on_theme_change, init=False)
        self._apply_theme()
        self._refresh_options()
        self.query_one("#branch-picker-filter", Input).focus()

    def _on_theme_change(self, old_value: str, new_value: str) -> None:
        self._apply_theme()
        self._refresh_options()

    def _apply_theme(self) -> None:
        palette = resolve_theme_palette(self.app.current_theme)
        self.styles.background = palette.overlay

        container = self.query_one("#branch-picker", Static)
        container.styles.background = palette.panel
        container.styles.color = palette.foreground
        container.styles.border = ("round", palette.primary)

        branch_filter = self.query_one("#branch-picker-filter", Input)
        branch_filter.styles.background = palette.surface
        branch_filter.styles.color = palette.foreground

        options = self.query_one("#branch-picker-options", OptionList)
        options.styles.background = palette.surface

    def _build_option(self, branch: str) -> Option:
        palette = resolve_theme_palette(self.app.current_theme)
        prompt = Text()
        prompt.append(truncate_middle(branch, 42), style=make_style(palette.foreground))
        if branch == self.current_branch:
            prompt.append("  current", style=make_style(palette.primary, bold=True))
        return Option(prompt, id=branch)

    def _refresh_options(self) -> None:
        options = self.query_one("#branch-picker-options", OptionList)
        if not self.filtered_branches:
            palette = resolve_theme_palette(self.app.current_theme)
            options.set_options(
                [Option(Text("No matching branches", style=make_style(palette.muted)), disabled=True)]
            )
            options.highlighted = None
            self.highlighted_branch = ""
            return

        if self.highlighted_branch not in self.filtered_branches:
            self.highlighted_branch = self.filtered_branches[0]

        options.set_options([self._build_option(branch) for branch in self.filtered_branches])
        options.highlighted = self.filtered_branches.index(self.highlighted_branch)
        options.scroll_to_highlight(top=False)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "branch-picker-filter":
            return
        query = event.value.strip()
        if not query:
            self.filtered_branches = self.branches[:]
            self.highlighted_branch = (
                self.current_branch
                if self.current_branch in self.filtered_branches
                else (self.filtered_branches[0] if self.filtered_branches else "")
            )
            self._refresh_options()
            return

        ranked: list[tuple[int, str]] = []
        for branch in self.branches:
            score = fuzzy_score(query, branch)
            if score is not None:
                ranked.append((score, branch))
        ranked.sort(key=lambda item: (item[0], item[1]))
        self.filtered_branches = [branch for _score, branch in ranked]
        if self.highlighted_branch in self.filtered_branches:
            pass
        elif self.filtered_branches:
            self.highlighted_branch = self.filtered_branches[0]
        else:
            self.highlighted_branch = ""
        self._refresh_options()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "branch-picker-filter":
            return
        if self.highlighted_branch:
            self.dismiss(self.highlighted_branch)

    def on_option_list_option_highlighted(
        self,
        event: OptionList.OptionHighlighted,
    ) -> None:
        if event.option_list.id != "branch-picker-options":
            return
        if event.option_id is not None:
            self.highlighted_branch = str(event.option_id)

    def on_option_list_option_selected(
        self,
        event: OptionList.OptionSelected,
    ) -> None:
        if event.option_list.id != "branch-picker-options":
            return
        if event.option_id is not None:
            self.dismiss(str(event.option_id))

    def action_cursor_up(self) -> None:
        self.query_one("#branch-picker-options", OptionList).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one("#branch-picker-options", OptionList).action_cursor_down()

    def action_choose_branch(self) -> None:
        if self.highlighted_branch:
            self.dismiss(self.highlighted_branch)

    def action_cancel(self) -> None:
        self.dismiss(None)
