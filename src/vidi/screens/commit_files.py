"""Modal listing the files in a commit's tree; open one, filter, or step commits."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, OptionList, Static
from textual.widgets.option_list import Option

from ..repository import RepoService
from ..theme import make_style, resolve_theme_palette
from .file_view import FileViewScreen


class CommitFilesScreen(ModalScreen[None]):
    """Browse a commit's files: Enter opens one, / filters, [ / ] step commits.

    With ``language`` set, lists just that language's files at the commit;
    without, the whole tree. Files changed in the commit are marked with a dot.
    The commit list is newest-first (as everywhere else); commits are numbered
    oldest-first, so ``]`` (newer) counts up and ``[`` (older) counts down.
    """

    MOVE_GROUP = Binding.Group("Move", compact=True)
    COMMIT_GROUP = Binding.Group("Commit", compact=True)
    BACK_GROUP = Binding.Group("Back", compact=True)

    CSS = """
    CommitFilesScreen {
        align: center middle;
    }

    #commit-files-box {
        width: 72;
        max-width: 90%;
        height: auto;
        max-height: 80%;
        padding: 0 2 1 2;
        border: round $primary;
        background: $panel;
    }

    #commit-files-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #commit-files-filter {
        display: none;
        margin-bottom: 1;
        border: solid $border;
        background: $surface;
        color: $text;
    }

    #commit-files-filter:focus {
        border: solid $primary;
    }

    #commit-files-list {
        height: auto;
        max-height: 22;
    }
    """

    BINDINGS = [
        Binding("down", "cursor_down", "Down", priority=True, group=MOVE_GROUP),
        Binding("up", "cursor_up", "Up", priority=True, group=MOVE_GROUP),
        Binding("enter", "open_file", "Open", priority=True),
        Binding("slash", "open_filter", "Filter"),
        Binding("[", "prev_commit", "Prev Commit", group=COMMIT_GROUP),
        Binding("]", "next_commit", "Next Commit", group=COMMIT_GROUP),
        Binding("escape", "close", "Back", group=BACK_GROUP),
        Binding("q", "close", "Back", show=False, group=BACK_GROUP),
    ]

    def __init__(
        self,
        service: RepoService,
        commit_hashes: list[str],
        index: int,
        language: str | None = None,
    ) -> None:
        super().__init__()
        self._service = service
        self._commit_hashes = list(commit_hashes) or [""]
        self._index = max(0, min(index, len(self._commit_hashes) - 1))
        self._language = language
        self._all_files: list[str] = []
        self._files: list[str] = []
        self._changed: set[str] = set()
        self._query = ""

    @property
    def _commit_hash(self) -> str:
        return self._commit_hashes[self._index]

    def compose(self) -> ComposeResult:
        with Vertical(id="commit-files-box"):
            yield Static(id="commit-files-title")
            yield Input(placeholder="Filter files", id="commit-files-filter")
            yield OptionList(id="commit-files-list")
        yield Footer()

    def on_mount(self) -> None:
        self.styles.background = resolve_theme_palette(self.app.current_theme).overlay
        self._reload()
        self._options().focus()

    def _options(self) -> OptionList:
        return self.query_one("#commit-files-list", OptionList)

    def _filter_input(self) -> Input:
        return self.query_one("#commit-files-filter", Input)

    def _files_for_current(self) -> list[str]:
        if self._language is not None:
            _, files_by_language = self._service.get_commit_language_stats(self._commit_hash)
            return sorted(files_by_language.get(self._language, []))
        return list(self._service.get_commit_files(self._commit_hash))

    def _reload(self) -> None:
        """Re-read the current commit's files + change set, then render."""
        self._all_files = self._files_for_current()
        self._changed = {
            change.path
            for change in self._service.get_commit_detail(self._commit_hash).file_changes
        }
        self._render_options()

    def _render_options(self) -> None:
        """Apply the active filter and rebuild the list + title (no git calls)."""
        palette = resolve_theme_palette(self.app.current_theme)
        query = self._query.strip().lower()
        self._files = (
            [path for path in self._all_files if query in path.lower()]
            if query
            else list(self._all_files)
        )
        self._update_title(palette)

        options = self._options()
        if options.option_count:
            options.clear_options()
        options.add_options([self._option_for(path, palette) for path in self._files])
        if options.option_count:
            options.highlighted = 0

    def _option_for(self, path: str, palette) -> Option:
        prompt = Text()
        if path in self._changed:
            prompt.append("● ", style=make_style(palette.primary, bold=True))
        else:
            prompt.append("  ")
        prompt.append(path, style=make_style(palette.foreground))
        return Option(prompt, id=path)

    def _update_title(self, palette) -> None:
        total = len(self._all_files)
        shown = len(self._files)
        word = "file" if total == 1 else "files"
        if self._query.strip():
            count = f"{shown} of {total} {word}"
        else:
            count = f"{total} {word}"

        title = Text()
        label = self._language if self._language is not None else "Files"
        title.append(f"{label}  ", style=make_style(palette.foreground, bold=True))
        detail = f"{count} in commit {self._commit_hash[:7]}"
        if len(self._commit_hashes) > 1:
            # commits numbered oldest-first (oldest = 1); list is newest-first
            total_commits = len(self._commit_hashes)
            detail += f"  ({total_commits - self._index}/{total_commits})"
        title.append(detail, style=make_style(palette.muted))
        if any(path in self._changed for path in self._files):
            title.append("    ● changed here", style=make_style(palette.primary))
        self.query_one("#commit-files-title", Static).update(title)

    # -- filtering ---------------------------------------------------------

    def action_open_filter(self) -> None:
        inp = self._filter_input()
        inp.display = True
        inp.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "commit-files-filter":
            self._query = event.value
            self._render_options()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Enter in the filter keeps the results and moves into the list.
        if event.input.id == "commit-files-filter":
            self._options().focus()

    def _close_filter(self) -> None:
        inp = self._filter_input()
        inp.value = ""
        inp.display = False
        self._query = ""
        self._render_options()
        self._options().focus()

    # -- navigation --------------------------------------------------------

    def action_cursor_down(self) -> None:
        self._options().action_cursor_down()

    def action_cursor_up(self) -> None:
        self._options().action_cursor_up()

    def action_next_commit(self) -> None:
        # newest-first list: "next" (newer) steps toward index 0
        self._go_to_commit(self._index - 1)

    def action_prev_commit(self) -> None:
        self._go_to_commit(self._index + 1)

    def _go_to_commit(self, index: int) -> None:
        if 0 <= index < len(self._commit_hashes) and index != self._index:
            self._index = index
            self._reload()  # re-applies the active filter to the new commit

    def action_open_file(self) -> None:
        # While typing in the filter, Enter confirms it instead of opening.
        if self._filter_input().has_focus:
            self._options().focus()
            return
        options = self._options()
        if options.highlighted is not None:
            self._open(options.get_option_at_index(options.highlighted).id)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._open(event.option.id)

    def _open(self, path: str | None) -> None:
        if path and path in self._files:
            self.app.push_screen(
                FileViewScreen(
                    self._service, self._commit_hash, self._files, self._files.index(path)
                )
            )

    def action_close(self) -> None:
        # Esc closes an open filter first, then the screen.
        if self._filter_input().display:
            self._close_filter()
            return
        self.dismiss()
