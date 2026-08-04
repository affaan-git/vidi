"""Modal listing the files in a commit's tree; open one, or step commits."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

from ..repository import RepoService
from ..theme import make_style, resolve_theme_palette
from .file_view import FileViewScreen


class CommitFilesScreen(ModalScreen[None]):
    """Browse a commit's files: Enter opens one, [ / ] step between commits.

    With ``language`` set, lists just that language's files at the commit;
    without, the whole tree. The commit list is newest-first (as everywhere
    else), so ``]`` steps to the newer commit and ``[`` to the older one.
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

    #commit-files-list {
        height: auto;
        max-height: 22;
    }
    """

    BINDINGS = [
        Binding("down", "cursor_down", "Down", priority=True, group=MOVE_GROUP),
        Binding("up", "cursor_up", "Up", priority=True, group=MOVE_GROUP),
        Binding("enter", "open_file", "Open", priority=True),
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
        self._files: list[str] = []

    @property
    def _commit_hash(self) -> str:
        return self._commit_hashes[self._index]

    def compose(self) -> ComposeResult:
        with Vertical(id="commit-files-box"):
            yield Static(id="commit-files-title")
            yield OptionList(id="commit-files-list")
        yield Footer()

    def on_mount(self) -> None:
        self.styles.background = resolve_theme_palette(self.app.current_theme).overlay
        self._reload()
        self._options().focus()

    def _options(self) -> OptionList:
        return self.query_one("#commit-files-list", OptionList)

    def _files_for_current(self) -> list[str]:
        if self._language is not None:
            _, files_by_language = self._service.get_commit_language_stats(self._commit_hash)
            return sorted(files_by_language.get(self._language, []))
        return list(self._service.get_commit_files(self._commit_hash))

    def _reload(self) -> None:
        palette = resolve_theme_palette(self.app.current_theme)
        self._files = self._files_for_current()
        count = len(self._files)
        word = "file" if count == 1 else "files"

        title = Text()
        label = self._language if self._language is not None else "Files"
        title.append(f"{label}  ", style=make_style(palette.foreground, bold=True))
        detail = f"{count} {word} in commit {self._commit_hash[:7]}"
        if len(self._commit_hashes) > 1:
            detail += f"  ({self._index + 1}/{len(self._commit_hashes)})"
        title.append(detail, style=make_style(palette.muted))
        self.query_one("#commit-files-title", Static).update(title)

        options = self._options()
        if options.option_count:
            options.clear_options()
        options.add_options([Option(path, id=path) for path in self._files])
        if options.option_count:
            options.highlighted = 0

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
            self._reload()

    def action_open_file(self) -> None:
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
        self.dismiss()
