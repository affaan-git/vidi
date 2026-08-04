"""Commit detail modal screen."""

from __future__ import annotations

from functools import partial

from rich.console import Console
from rich.table import Table
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, Static

from ..data import compact_label_for_change, section_for_change
from ..formatting import build_repo_stats_sub_title
from ..models import CommitFileChange, CommitPatchSection, RepoContext, RepoScanSnapshot
from ..repository import RepoService
from ..theme import make_change_stat_style, make_style, resolve_theme_palette
from ..utils import rel_time, utc_now
from .commit_files import CommitFilesScreen

# Coalesce the resize event burst so the view re-renders once per settle.
RESIZE_DEBOUNCE_SECONDS = 0.05


class CommitDetailScreen(ModalScreen[None]):
    """Full-screen modal for a single commit."""

    BACK_GROUP = Binding.Group("Back", compact=True)
    PANE_GROUP = Binding.Group("Pane")
    MOVE_GROUP = Binding.Group("Move", compact=True)
    PAGE_GROUP = Binding.Group("Page", compact=True)
    JUMP_GROUP = Binding.Group("Jump", compact=True)
    COMMIT_GROUP = Binding.Group("Commit", compact=True)
    SEARCH_GROUP = Binding.Group("Search", compact=True)

    CSS = """
    CommitDetailScreen {
        background: $background;
    }

    #commit-detail-summary-bar {
        padding: 0 1;
        background: $surface;
        border: solid $border;
    }

    #commit-detail-body {
        layout: horizontal;
        height: 1fr;
        margin: 0 1 1 1;
    }

    .commit-detail-panel {
        padding: 1 1;
        background: $panel;
        border: solid $border;
    }

    #commit-detail-overview-panel:focus-within,
    #commit-detail-files-panel:focus-within,
    #commit-detail-patch-panel:focus-within {
        border: solid $primary;
    }

    .commit-detail-panel-title {
        text-style: bold;
        margin-bottom: 0;
    }

    #commit-detail-sidebar {
        width: 36;
        margin-right: 1;
        height: 1fr;
    }

    #commit-detail-overview-panel {
        height: 1fr;
        max-height: 12;
        min-height: 5;
        margin-bottom: 1;
    }

    #commit-detail-overview-scroll {
        height: 1fr;
        overflow-y: auto;
        scrollbar-gutter: stable;
    }

    #commit-detail-files-panel {
        height: 1fr;
        min-height: 6;
    }

    #commit-detail-files-title {
        margin-bottom: 0;
    }

    #commit-detail-files-meta {
        margin-bottom: 0;
        height: 1;
    }

    #commit-detail-files-diff,
    #commit-detail-files-outline {
        height: 1fr;
        min-height: 3;
    }

    #commit-detail-main {
        width: 1fr;
        height: 1fr;
    }

    #commit-detail-patch-panel {
        height: 1fr;
    }

    #commit-detail-search,
    #commit-detail-files-filter {
        display: none;
        margin-bottom: 1;
        border: solid $border;
        background: $surface;
        color: $text;
    }

    #commit-detail-search:focus,
    #commit-detail-files-filter:focus {
        border: solid $primary;
    }

    #commit-detail-selected-scroll,
    #commit-detail-full-scroll {
        height: 1fr;
        overflow-y: auto;
        scrollbar-gutter: stable;
    }

    #commit-detail-selected-content,
    #commit-detail-full-content {
        height: auto;
        min-height: 1;
    }

    DataTable {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("down", "cursor_down", "Down", priority=True, group=MOVE_GROUP),
        Binding("up", "cursor_up", "Up", priority=True, group=MOVE_GROUP),
        Binding("j", "cursor_down", "Down", priority=True, show=False),
        Binding("k", "cursor_up", "Up", priority=True, show=False),
        Binding("tab", "focus_next_pane", "Next Pane", priority=True, group=PANE_GROUP),
        Binding(
            "shift+tab",
            "focus_prev_pane",
            "Prev Pane",
            priority=True,
            key_display="S-Tab",
            group=PANE_GROUP,
        ),
        Binding("[", "prev_commit", "Prev Commit", group=COMMIT_GROUP),
        Binding("]", "next_commit", "Next Commit", group=COMMIT_GROUP),
        Binding("pageup", "page_up", "Page Up", priority=True, group=PAGE_GROUP),
        Binding("pagedown", "page_down", "Page Down", priority=True, group=PAGE_GROUP),
        Binding("home", "scroll_top", "Top", priority=True, group=JUMP_GROUP),
        Binding("end", "scroll_bottom", "Bottom", priority=True, group=JUMP_GROUP),
        Binding("p", "toggle_patch_scope", "Patch"),
        Binding("f", "browse_files", "Files"),
        Binding("slash", "open_search", "Search"),
        Binding("n", "next_match", "Next Match", group=SEARCH_GROUP),
        Binding("N", "prev_match", "Prev Match", group=SEARCH_GROUP),
        Binding("escape", "close_screen", "Back", group=BACK_GROUP),
        Binding("q", "close_screen", "Back", show=False, group=BACK_GROUP),
    ]

    DEFAULT_PANE = "files"

    def __init__(
        self,
        service: RepoService,
        commit_hash: str,
        source_branch: str | None = None,
        commit_hashes: list[str] | None = None,
        show_full_patch: bool = False,
    ) -> None:
        super().__init__()
        self.service = service
        self.commit_hash = commit_hash
        self.source_branch = source_branch
        # ordered sibling commits (newest first) so [ / ] can step between them
        self._commit_hashes = list(commit_hashes) if commit_hashes else [commit_hash]
        if commit_hash in self._commit_hashes:
            self._commit_index = self._commit_hashes.index(commit_hash)
        else:
            self._commit_hashes = [commit_hash]
            self._commit_index = 0
        self.detail = service.get_commit_detail(commit_hash)
        self.show_full_patch = show_full_patch
        self.active_pane = self.DEFAULT_PANE
        self.selected_file_index = 0
        self._suspend_file_events = False
        self._patch_section_offsets: dict[CommitPatchSection, int] = {}
        self._outline_offsets: list[tuple[int, int]] = []
        self._ignore_next_patch_scroll_sync = False
        self._reconcile_revision = 0
        self._pending_focus_target: str | None = None
        self._pending_focus_origin_id: str | None = None
        self._pending_patch_scroll_to_selection = False
        self._files_table_signatures: dict[str, tuple[int, int, int] | None] = {
            "commit-detail-files-diff": None,
            "commit-detail-files-outline": None,
        }
        self._mode_transition_active = False
        self._full_patch_ready = False
        self._force_requested_focus = False
        self._resize_timer = None
        self._theme_palette = resolve_theme_palette(self.app.current_theme)
        # in-patch search state
        self._search_query = ""
        self._match_lines: list[int] = []
        self._current_match = -1
        self._patch_base: dict[str, Text] = {"selected": Text(), "full": Text()}
        self._patch_offsets: list[int] | None = None
        self._patch_offsets_key: tuple[str, int, int] = ("", 0, 0)
        # changed-files filter state (diff mode only)
        self._files_query = ""

    def action_next_commit(self) -> None:
        # the list is newest-first, so "next" (newer) steps toward index 0,
        # matching the main screen where ] moves to the newer year
        self._go_to_commit(self._commit_index - 1)

    def action_prev_commit(self) -> None:
        self._go_to_commit(self._commit_index + 1)

    def action_browse_files(self) -> None:
        """Browse every file in this commit's tree, stepping commits with [ / ]."""
        self.app.push_screen(
            CommitFilesScreen(self.service, self._commit_hashes, self._commit_index)
        )

    def _go_to_commit(self, index: int) -> None:
        # Step in place rather than swapping in a fresh screen: the commit detail
        # is already cached, so reusing this screen avoids a full compose/mount
        # rebuild on every keypress and keeps fast [ / ] stepping responsive.
        if not 0 <= index < len(self._commit_hashes) or index == self._commit_index:
            return
        self._commit_index = index
        self.commit_hash = self._commit_hashes[index]
        self.detail = self.service.get_commit_detail(self.commit_hash)
        self.selected_file_index = 0
        for table_id in self._files_table_signatures:
            self._files_table_signatures[table_id] = None
        self._ignore_next_patch_scroll_sync = False
        self._full_patch_ready = False
        self._render_detail_view(
            focus_target="patch" if self.show_full_patch else self.DEFAULT_PANE,
            scroll_to_selection=self.show_full_patch,
        )
        self._reset_detail_scroll()

    def _reset_detail_scroll(self) -> None:
        self.query_one("#commit-detail-overview-scroll", VerticalScroll).scroll_home(
            animate=False
        )
        self._selected_patch_scroll().scroll_home(animate=False)
        self._full_patch_scroll().scroll_home(animate=False)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Static(id="commit-detail-summary-bar"):
            yield Static(id="commit-detail-summary-content")
        with Horizontal(id="commit-detail-body"):
            with Vertical(id="commit-detail-sidebar"):
                with Vertical(
                    id="commit-detail-overview-panel",
                    classes="commit-detail-panel",
                ):
                    yield Static("Commit Overview", classes="commit-detail-panel-title")
                    with VerticalScroll(id="commit-detail-overview-scroll"):
                        yield Static(id="commit-detail-overview")
                with Vertical(
                    id="commit-detail-files-panel",
                    classes="commit-detail-panel",
                ):
                    yield Static(id="commit-detail-files-title", classes="commit-detail-panel-title")
                    yield Static(id="commit-detail-files-meta")
                    yield Input(placeholder="Filter files", id="commit-detail-files-filter")
                    yield DataTable(id="commit-detail-files-diff", show_row_labels=False)
                    yield DataTable(id="commit-detail-files-outline", show_row_labels=False)
            with Vertical(id="commit-detail-main"):
                with Vertical(
                    id="commit-detail-patch-panel",
                    classes="commit-detail-panel",
                ):
                    yield Static(id="commit-detail-patch-title", classes="commit-detail-panel-title")
                    yield Input(placeholder="Search patch", id="commit-detail-search")
                    with VerticalScroll(id="commit-detail-selected-scroll"):
                        yield Static(id="commit-detail-selected-content")
                    with VerticalScroll(id="commit-detail-full-scroll"):
                        yield Static(id="commit-detail-full-content")
        yield Footer()

    def on_mount(self) -> None:
        self.watch(self.app, "theme", self._on_theme_change, init=False)
        diff_table = self.query_one("#commit-detail-files-diff", DataTable)
        outline_table = self.query_one("#commit-detail-files-outline", DataTable)
        self.watch(self._full_patch_scroll(), "scroll_y", self._on_full_patch_scroll_y, init=False)
        for table in (diff_table, outline_table):
            table.cursor_type = "row"
            table.show_horizontal_scrollbar = False
        diff_table.add_column("File", width=18, key="file")
        diff_table.add_column("+", width=4, key="adds")
        diff_table.add_column("-", width=4, key="dels")
        outline_table.add_column("File", width=18, key="file")
        outline_table.add_column("+", width=4, key="adds")
        outline_table.add_column("-", width=4, key="dels")
        self.sub_title = self._build_repo_stats_sub_title()
        self._apply_theme()
        self._render_detail_view(
            focus_target="patch" if self.show_full_patch else self.DEFAULT_PANE
        )

    def _on_theme_change(self, old_value: str, new_value: str) -> None:
        self._apply_theme()
        self._render_detail_view()

    def on_resize(self, event: events.Resize) -> None:
        if not self.is_mounted:
            return
        # Coalesce the resize burst, and don't rebuild the (width-independent)
        # patch text on resize; only the layout and table columns need to react.
        if self._resize_timer is not None:
            self._resize_timer.stop()
        self._resize_timer = self.set_timer(RESIZE_DEBOUNCE_SECONDS, self._render_after_resize)

    def _render_after_resize(self) -> None:
        self._resize_timer = None
        if self.is_mounted:
            self._render_detail_view(refresh_patch_content=False)

    def _focus_targets(self) -> tuple[VerticalScroll | DataTable, ...]:
        return tuple(self._pane_target(pane) for pane in self._available_panes())

    def _selected_patch_scroll(self) -> VerticalScroll:
        return self.query_one("#commit-detail-selected-scroll", VerticalScroll)

    def _full_patch_scroll(self) -> VerticalScroll:
        return self.query_one("#commit-detail-full-scroll", VerticalScroll)

    def _active_patch_scroll(self) -> VerticalScroll:
        return self._full_patch_scroll() if self.show_full_patch else self._selected_patch_scroll()

    def _pane_target(self, pane: str) -> VerticalScroll | DataTable:
        targets: dict[str, VerticalScroll | DataTable] = {
            "overview": self.query_one("#commit-detail-overview-scroll", VerticalScroll),
            "files": self._current_files_table(),
            "patch": self._active_patch_scroll(),
        }
        return targets[pane]

    def _available_panes(self) -> tuple[str, ...]:
        # Fixed focus stops per mode; Tab cycles them whether or not a scroll
        # currently overflows. The overview is hidden in full-patch mode.
        if self.show_full_patch:
            return ("files", "patch")
        return ("overview", "files", "patch")

    def _sync_active_pane_from_focus(self) -> None:
        if self._mode_transition_active or self._pending_focus_target is not None:
            return
        focused_id = getattr(self.focused, "id", None)
        mapping = {
            "commit-detail-overview-scroll": "overview",
            "commit-detail-files-diff": "files",
            "commit-detail-files-outline": "files",
            "commit-detail-selected-scroll": "patch",
            "commit-detail-full-scroll": "patch",
        }
        pane = mapping.get(focused_id)
        if pane in self._available_panes():
            self.active_pane = pane

    def _focus_active_pane(self) -> None:
        panes = self._available_panes()
        if not panes:
            return
        if self.active_pane not in panes:
            self.active_pane = "patch" if self.show_full_patch and "patch" in panes else panes[0]
        target = self._pane_target(self.active_pane)
        if self.focused is not target:
            target.focus()

    def _refresh_scroll_focusability(self) -> None:
        overview_scroll = self.query_one("#commit-detail-overview-scroll", VerticalScroll)
        selected_patch_scroll = self._selected_patch_scroll()
        full_patch_scroll = self._full_patch_scroll()

        # Only the panes for the current mode are focus stops; the inactive
        # patch scroll stays unfocusable so Tab never lands on the hidden one.
        overview_scroll.can_focus = not self.show_full_patch
        selected_patch_scroll.can_focus = not self.show_full_patch
        full_patch_scroll.can_focus = self.show_full_patch
        available = self._available_panes()
        preferred = "patch" if self.show_full_patch else self.DEFAULT_PANE
        pending = self._pending_focus_target
        if pending == self.active_pane:
            preferred = pending
        if self.active_pane not in available:
            if preferred == pending and pending is not None:
                return
            self.active_pane = preferred if preferred in available else available[0]

    def _apply_theme(self) -> None:
        # Backgrounds and borders (including the :focus-within pane highlight)
        # come from the registered theme via CSS; only the muted title color is
        # not a standard theme variable.
        self._theme_palette = resolve_theme_palette(self.app.current_theme)
        for title in self.query(".commit-detail-panel-title"):
            title.styles.color = self._theme_palette.muted

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        self._sync_active_pane_from_focus()

    def _apply_detail_layout(self) -> None:
        sidebar = self.query_one("#commit-detail-sidebar", Vertical)
        overview_panel = self.query_one("#commit-detail-overview-panel", Vertical)

        if self.show_full_patch:
            sidebar.styles.width = 28
            overview_panel.styles.display = "none"
        else:
            sidebar.styles.width = 32
            overview_panel.styles.display = "block"

    def _ensure_valid_focus(self, *, prefer_patch: bool = False) -> None:
        preferred = "patch" if prefer_patch else self.active_pane
        available = self._available_panes()
        if preferred in available:
            self.active_pane = preferred
        elif available:
            self.active_pane = available[0]
        self._focus_active_pane()

    def _files_table_is_measured(self, table: DataTable | None = None) -> bool:
        table = table or self._current_files_table()
        return bool(table.size.width or table.content_region.width)

    def _diff_files_table(self) -> DataTable:
        return self.query_one("#commit-detail-files-diff", DataTable)

    def _outline_files_table(self) -> DataTable:
        return self.query_one("#commit-detail-files-outline", DataTable)

    def _current_files_table(self) -> DataTable:
        return self._outline_files_table() if self.show_full_patch else self._diff_files_table()

    def _refresh_files_table(self, *, force: bool = False) -> None:
        table = self._current_files_table()
        if not self._files_table_is_measured(table):
            return
        signature = self._files_table_layout(table)
        table_id = table.id or ""
        if (
            not force
            and signature == self._files_table_signatures.get(table_id)
            and table.row_count == len(self._visible_file_indices())
        ):
            self._sync_outline_cursor(self.selected_file_index)
            return
        self._update_files_table()
        self._files_table_signatures[table_id] = signature

    def _focus_widget_for_name(self, name: str | None) -> VerticalScroll | DataTable | None:
        if name == "overview":
            return self.query_one("#commit-detail-overview-scroll", VerticalScroll)
        if name == "patch":
            return self._active_patch_scroll()
        if name == "files":
            return self._current_files_table()
        return None

    def _schedule_reconcile(
        self,
        *,
        focus_target: str | None = None,
        scroll_to_selection: bool = False,
        force_files_table_refresh: bool = False,
    ) -> None:
        if focus_target is not None:
            self._pending_focus_target = focus_target
            self._pending_focus_origin_id = getattr(self.focused, "id", None)
        self._pending_patch_scroll_to_selection = (
            self._pending_patch_scroll_to_selection or scroll_to_selection
        )
        if force_files_table_refresh:
            for table_id in self._files_table_signatures:
                self._files_table_signatures[table_id] = None
        self._reconcile_revision += 1
        revision = self._reconcile_revision
        self.call_after_refresh(partial(self._reconcile_after_refresh, revision))

    def _reconcile_after_refresh(self, revision: int) -> None:
        if not self.is_mounted or revision != self._reconcile_revision:
            return

        self._refresh_files_table()
        self._refresh_scroll_focusability()

        if self._pending_patch_scroll_to_selection and self.show_full_patch:
            self._scroll_patch_to_selected()
        self._pending_patch_scroll_to_selection = False

        requested_focus = self._focus_widget_for_name(self._pending_focus_target)
        targets = self._focus_targets()
        if (
            requested_focus in targets
            and self.focused is not requested_focus
            and (
                self._force_requested_focus
                or self.focused not in targets
                or getattr(self.focused, "id", None) == self._pending_focus_origin_id
            )
        ):
            requested_pane = self._pending_focus_target
            if requested_pane is not None:
                self.active_pane = requested_pane
            requested_focus.focus()
        elif self.focused not in targets:
            self._ensure_valid_focus(prefer_patch=self.show_full_patch)
        else:
            self._sync_active_pane_from_focus()

        self._pending_focus_target = None
        self._pending_focus_origin_id = None
        self._mode_transition_active = False
        self._force_requested_focus = False

    def _render_detail_view(
        self,
        *,
        focus_target: str | None = None,
        scroll_to_selection: bool = False,
        refresh_patch_content: bool = True,
    ) -> None:
        if self.detail.file_changes and not (
            0 <= self.selected_file_index < len(self.detail.file_changes)
        ):
            self.selected_file_index = 0
        if focus_target is not None:
            self.active_pane = focus_target
        self._apply_detail_layout()
        self._diff_files_table().styles.display = "none" if self.show_full_patch else "block"
        self._outline_files_table().styles.display = "block" if self.show_full_patch else "none"
        self._selected_patch_scroll().styles.display = "none" if self.show_full_patch else "block"
        self._full_patch_scroll().styles.display = "block" if self.show_full_patch else "none"

        palette = self._theme_palette
        now = utc_now()

        summary_bar = Text()
        summary_bar.append("Commit ", style=make_style(palette.muted, bold=True))
        summary_bar.append(
            self.detail.commit_hash[:12],
            style=make_style(palette.foreground, bold=True),
        )
        summary_bar.append("  |  ", style=make_style(palette.muted))
        summary_bar.append(self.detail.author_name, style=make_style(palette.foreground))
        summary_bar.append("  |  ", style=make_style(palette.muted))
        summary_bar.append(
            self.detail.authored_at.strftime("%Y-%m-%d %H:%M"),
            style=make_style(palette.muted),
        )
        summary_bar.append("  |  ", style=make_style(palette.muted))
        summary_bar.append(
            rel_time(self.detail.authored_at, now),
            style=make_style(palette.muted),
        )
        self.query_one("#commit-detail-summary-content", Static).update(summary_bar)

        overview = Text()
        overview.append(
            f"{self.detail.subject}\n",
            style=make_style(palette.foreground, bold=True),
        )
        if self.detail.body:
            overview.append(f"{self.detail.body}\n\n", style=make_style(palette.foreground))
        overview.append("Author Email\n", style=make_style(palette.muted, bold=True))
        overview.append(
            f"{self.detail.author_email}\n",
            style=make_style(palette.foreground),
        )
        if self.detail.parents:
            overview.append("\nParents\n", style=make_style(palette.muted, bold=True))
            overview.append(
                ", ".join(parent[:7] for parent in self.detail.parents),
                style=make_style(palette.foreground),
            )
        self.query_one("#commit-detail-overview", Static).update(overview)

        current_table = self._current_files_table()
        if not self._files_table_is_measured(current_table):
            self._current_files_table().clear(columns=False)
            table_id = current_table.id or ""
            self._files_table_signatures[table_id] = None
        else:
            self._refresh_files_table(force=False)
        files_title = (
            "Patch Outline"
            if self.show_full_patch
            else "Files Changed"
        )
        self.query_one("#commit-detail-files-title", Static).update(files_title)
        self._render_files_meta()

        self._render_patch_panel(refresh_content=refresh_patch_content)
        self._schedule_reconcile(
            focus_target=focus_target,
            scroll_to_selection=scroll_to_selection,
            force_files_table_refresh=False,
        )

    def _render_files_meta(self) -> None:
        palette = self._theme_palette
        total = len(self.detail.file_changes)
        total_additions = sum(change.additions or 0 for change in self.detail.file_changes)
        total_deletions = sum(change.deletions or 0 for change in self.detail.file_changes)
        file_word = "file" if total == 1 else "files"

        files_meta = Text()
        if self._files_query.strip() and not self.show_full_patch:
            shown = len(self._visible_file_indices())
            files_meta.append(
                f"{shown} of {total} {file_word}",
                style=make_style(palette.foreground, bold=True),
            )
        else:
            files_meta.append(
                f"{total} {file_word}",
                style=make_style(palette.foreground, bold=True),
            )
        files_meta.append("  ", style=make_style(palette.muted))
        files_meta.append(
            f"+{total_additions}",
            style=make_change_stat_style(palette, total_additions, positive=True),
        )
        files_meta.append("  ", style=make_style(palette.muted))
        files_meta.append(
            f"-{total_deletions}",
            style=make_change_stat_style(palette, total_deletions, positive=False),
        )
        if self.show_full_patch:
            files_meta.append("  ", style=make_style(palette.muted))
            files_meta.append(
                "Select to jump",
                style=make_style(palette.muted),
            )
        self.query_one("#commit-detail-files-meta", Static).update(files_meta)

    def _build_repo_stats_sub_title(self) -> str:
        context = self.app.context
        return build_repo_stats_sub_title(context, self.service.scanned_file_count(context))

    def _files_table_layout(self, table: DataTable | None = None) -> tuple[int, int, int]:
        table = table or self._current_files_table()
        width = (
            table.scrollable_content_region.width
            or table.content_region.width
            or table.size.width
            or 28
        )
        # Reserve the table's own chrome: one cell of padding on each side of
        # the three columns (6) plus the stable scrollbar gutter (2). Undercount
        # here and the file column overflows, leaving a phantom horizontal scroll.
        chrome = 8
        stats_width = 4
        file_width = max(8, width - (stats_width * 2) - chrome)
        return file_width, stats_width, stats_width

    def _apply_files_table_layout(self) -> int:
        table = self._current_files_table()
        file_width, adds_width, dels_width = self._files_table_layout(table)
        table.columns["file"].width = file_width
        if "adds" in table.columns:
            table.columns["adds"].width = adds_width
        if "dels" in table.columns:
            table.columns["dels"].width = dels_width
        return file_width

    def _file_index_for_section(self, section: CommitPatchSection) -> int | None:
        for index, change in enumerate(self.detail.file_changes):
            if change.path in {section.path, section.old_path, section.new_path}:
                return index
        return None

    def _section_for_change(
        self,
        index: int,
        change: CommitFileChange,
    ) -> CommitPatchSection | None:
        return section_for_change(self.detail.patch_sections, index, change)

    def _display_label_for_file(self, index: int) -> str:
        if not (0 <= index < len(self.detail.file_changes)):
            return "No file selected"
        change = self.detail.file_changes[index]
        section = self._section_for_change(index, change)
        return compact_label_for_change(change, section, 56)

    def _build_stat_cell(
        self,
        value: int | None,
        *,
        positive: bool,
    ) -> Text:
        palette = self._theme_palette
        if value is None:
            return Text("-", style=make_style(palette.muted))
        return Text(
            str(value),
            style=make_change_stat_style(palette, value, positive=positive),
        )

    def _visible_patch_sections(self) -> tuple[CommitPatchSection, ...]:
        if self.show_full_patch:
            return self.detail.patch_sections
        if not self.detail.file_changes:
            return ()
        change = self.detail.file_changes[self.selected_file_index]
        section = self._section_for_change(self.selected_file_index, change)
        return (section,) if section is not None else ()

    def _patch_line_count(self, text: Text) -> int:
        if not text.plain:
            return 0
        return text.plain.count("\n") + 1

    def _build_patch_title(self) -> Table:
        palette = self._theme_palette
        title = Table.grid(expand=True)
        title.add_column(ratio=1)
        title.add_column(no_wrap=True, justify="right")

        current = Text()
        if self.show_full_patch:
            current.append("Full Patch", style=make_style(palette.muted, bold=True))
            current.append("  ", style=make_style(palette.muted))
            current.append(
                f"{len(self.detail.patch_sections)} files",
                style=make_style(palette.foreground),
            )
        else:
            current.append("Selected Diff", style=make_style(palette.muted, bold=True))
            if self.detail.file_changes:
                current.append("  ", style=make_style(palette.muted))
                current.append(
                    self._display_label_for_file(self.selected_file_index),
                    style=make_style(palette.foreground),
                )

        if self._search_query:
            current.append("  ", style=make_style(palette.muted))
            if self._match_lines:
                current.append(
                    f"{self._current_match + 1}/{len(self._match_lines)} matches",
                    style=make_style(palette.primary),
                )
            else:
                current.append("no matches", style=make_style(palette.muted))

        modes = Text()
        modes.append(
            "File Diff",
            style=make_style(
                palette.primary if not self.show_full_patch else palette.muted,
                bold=not self.show_full_patch,
            ),
        )
        modes.append(" · ", style=make_style(palette.muted))
        modes.append(
            "Full Patch",
            style=make_style(
                palette.primary if self.show_full_patch else palette.muted,
                bold=self.show_full_patch,
            ),
        )
        title.add_row(current, modes)
        return title

    def _render_patch_text(self, patch_text: str) -> Text:
        palette = self._theme_palette
        text = Text()
        patch_lines = patch_text.splitlines()
        for index, raw in enumerate(patch_lines):
            line = raw.rstrip("\n")
            style = make_style(palette.foreground)
            if (
                line.startswith("diff --git")
                or line.startswith("index ")
                or line.startswith("similarity index")
            ):
                style = make_style(palette.muted, bold=True)
            elif line.startswith("rename from ") or line.startswith("rename to "):
                style = make_style(palette.primary, bold=True)
            elif line.startswith("--- ") or line.startswith("+++ "):
                style = make_style(palette.muted)
            elif line.startswith("@@"):
                style = make_style(palette.primary, bold=True)
            elif line.startswith("+") and not line.startswith("+++"):
                style = make_style(palette.success)
            elif line.startswith("-") and not line.startswith("---"):
                style = make_style(palette.error)
            text.append(line, style=style)
            if index < len(patch_lines) - 1:
                text.append("\n")
        return text

    def _render_patch_panel(
        self,
        *,
        refresh_content: bool,
    ) -> None:
        palette = self._theme_palette
        self.query_one("#commit-detail-patch-title", Static).update(self._build_patch_title())

        if refresh_content:
            selected_content = Text()
            full_content = Text()
            self._patch_section_offsets = {}
            self._outline_offsets = []
            selected_sections = self._visible_patch_sections()
            if selected_sections:
                for section_index, section in enumerate(selected_sections):
                    rendered_patch = self._render_patch_text(section.patch)
                    selected_content.append(rendered_patch)
                    if section_index < len(selected_sections) - 1:
                        selected_content.append("\n\n")
            else:
                selected_content.append(
                    "No diff available for this selection.",
                    style=make_style(palette.muted, bold=True),
                )

            current_line = 0
            if self.detail.patch_sections:
                for section_index, section in enumerate(self.detail.patch_sections):
                    self._patch_section_offsets[section] = current_line
                    file_index = self._file_index_for_section(section)
                    if file_index is not None:
                        self._outline_offsets.append((current_line, file_index))
                    line = Text()
                    if section.is_rename:
                        line.append(
                            f"{section.old_path} -> {section.new_path}",
                            style=make_style(palette.foreground, bold=True),
                        )
                    else:
                        line.append(
                            section.path,
                            style=make_style(palette.foreground, bold=True),
                        )
                    if section.is_binary:
                        line.append("  [binary]", style=make_style(palette.muted, bold=True))
                    full_content.append(line)
                    full_content.append("\n")
                    current_line += 1

                    rendered_patch = self._render_patch_text(section.patch)
                    full_content.append(rendered_patch)
                    current_line += self._patch_line_count(rendered_patch)
                    if section_index < len(self.detail.patch_sections) - 1:
                        full_content.append("\n\n")
                        current_line += 2
            else:
                full_content.append(
                    "No patch available for this commit.",
                    style=make_style(palette.muted, bold=True),
                )

            self.query_one("#commit-detail-selected-content", Static).update(selected_content)
            self.query_one("#commit-detail-full-content", Static).update(full_content)
            # keep the un-highlighted texts so search can overlay onto a fresh copy
            self._patch_base = {"selected": selected_content, "full": full_content}
            if self._search_query:
                self._apply_patch_search(recompute=True)

    # -- in-patch search ---------------------------------------------------

    def _search_input(self) -> Input:
        return self.query_one("#commit-detail-search", Input)

    def _files_filter_input(self) -> Input:
        return self.query_one("#commit-detail-files-filter", Input)

    def _search_focused(self) -> bool:
        try:
            return self._search_input().has_focus or self._files_filter_input().has_focus
        except Exception:
            return False

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # While typing a search, the input needs every key; without this the
        # priority scroll/pane bindings would steal them.
        if self._search_focused() and action in (
            "cursor_down",
            "cursor_up",
            "page_down",
            "page_up",
            "scroll_top",
            "scroll_bottom",
            "focus_next_pane",
            "focus_prev_pane",
        ):
            return False
        return True

    def action_open_search(self) -> None:
        # Context-aware: filter the changed-files list from the files pane (diff
        # mode), search the patch from anywhere else.
        if self.active_pane == "files" and not self.show_full_patch:
            inp = self._files_filter_input()
            inp.display = True
            inp.focus()
            return
        inp = self._search_input()
        inp.placeholder = "Search patch" if self.show_full_patch else "Search diff"
        inp.display = True
        inp.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "commit-detail-search":
            self._search_query = event.value
            self._apply_patch_search(recompute=True)
            if self._match_lines:
                self._scroll_to_patch_match()
        elif event.input.id == "commit-detail-files-filter":
            self._files_query = event.value
            self._refresh_files_table(force=True)
            self._render_files_meta()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Enter keeps the filter/highlights and moves into the list or patch.
        if event.input.id == "commit-detail-search":
            self._active_patch_scroll().focus()
        elif event.input.id == "commit-detail-files-filter":
            self._current_files_table().focus()

    def _close_search(self) -> None:
        inp = self._search_input()
        inp.value = ""
        inp.display = False
        self._search_query = ""
        self._apply_patch_search(recompute=True)
        self._active_patch_scroll().focus()

    def _close_files_filter(self) -> None:
        inp = self._files_filter_input()
        inp.value = ""
        inp.display = False
        self._files_query = ""
        self._refresh_files_table(force=True)
        self._render_files_meta()
        self._current_files_table().focus()

    def _active_patch_key(self) -> str:
        return "full" if self.show_full_patch else "selected"

    def _recompute_patch_matches(self, base: Text, query: str) -> None:
        if query:
            lines = base.plain.split("\n")
            self._match_lines = [i for i, line in enumerate(lines) if query in line.lower()]
        else:
            self._match_lines = []
        self._current_match = 0 if self._match_lines else -1
        self._patch_offsets = None

    def _apply_patch_search(self, *, recompute: bool) -> None:
        key = self._active_patch_key()
        base = self._patch_base[key]
        query = self._search_query.lower()
        palette = self._theme_palette
        if recompute:
            self._recompute_patch_matches(base, query)

        display = base.copy()
        if query and self._match_lines:
            lines = base.plain.split("\n")
            all_style = make_style(palette.selected_fg, background=palette.selected_bg)
            current_style = make_style(palette.selected_fg, background=palette.primary, bold=True)
            current_line = (
                self._match_lines[self._current_match]
                if 0 <= self._current_match < len(self._match_lines)
                else -1
            )
            offset = 0
            for index, line in enumerate(lines):
                if query in line.lower():
                    style = current_style if index == current_line else all_style
                    lowered = line.lower()
                    pos = 0
                    while True:
                        found = lowered.find(query, pos)
                        if found < 0:
                            break
                        display.stylize(style, offset + found, offset + found + len(query))
                        pos = found + len(query)
                offset += len(line) + 1

        static_id = (
            "#commit-detail-full-content"
            if self.show_full_patch
            else "#commit-detail-selected-content"
        )
        self.query_one(static_id, Static).update(display)
        self.query_one("#commit-detail-patch-title", Static).update(self._build_patch_title())

    def _ensure_patch_offsets(self) -> None:
        key = self._active_patch_key()
        base = self._patch_base[key]
        static_id = (
            "#commit-detail-full-content"
            if self.show_full_patch
            else "#commit-detail-selected-content"
        )
        static = self.query_one(static_id, Static)
        width = max(1, static.content_region.width or static.size.width or 80)
        cache_key = (key, width, len(base.plain))
        if self._patch_offsets is not None and self._patch_offsets_key == cache_key:
            return
        console = Console(width=width)
        offsets: list[int] = []
        row = 0
        for line in base.plain.split("\n"):
            offsets.append(row)
            row += max(1, len(Text(line).wrap(console, width)))
        self._patch_offsets = offsets
        self._patch_offsets_key = cache_key

    def _scroll_to_patch_match(self) -> None:
        if not 0 <= self._current_match < len(self._match_lines):
            return
        self._ensure_patch_offsets()
        line = self._match_lines[self._current_match]
        assert self._patch_offsets is not None
        target = self._patch_offsets[line] if line < len(self._patch_offsets) else 0
        self._active_patch_scroll().scroll_to(y=max(0, target - 2), animate=False)

    def action_next_match(self) -> None:
        self._step_match(1)

    def action_prev_match(self) -> None:
        self._step_match(-1)

    def _step_match(self, delta: int) -> None:
        if not self._match_lines:
            return
        self._current_match = (self._current_match + delta) % len(self._match_lines)
        self._apply_patch_search(recompute=False)
        self._scroll_to_patch_match()

    def _scroll_patch_to_selected(self) -> None:
        if not self.show_full_patch or not self.detail.file_changes:
            return
        change = self.detail.file_changes[self.selected_file_index]
        section = self._section_for_change(self.selected_file_index, change)
        if section is None:
            return
        offset = self._patch_section_offsets.get(section)
        if offset is None:
            return
        patch_scroll = self._full_patch_scroll()
        target_y = max(offset - 1, 0)
        if int(patch_scroll.scroll_y) == target_y:
            self._ignore_next_patch_scroll_sync = False
            return
        self._ignore_next_patch_scroll_sync = True
        patch_scroll.scroll_to(
            y=target_y,
            animate=False,
        )

    def _sync_outline_cursor(self, index: int) -> None:
        if not (0 <= index < len(self.detail.file_changes)):
            return
        visible = self._visible_file_indices()
        if index not in visible:
            return
        row = visible.index(index)
        table = self._current_files_table()
        if table.cursor_row == row:
            return
        self._suspend_file_events = True
        try:
            table.move_cursor(row=row, animate=False, scroll=True)
        finally:
            self._suspend_file_events = False

    def _sync_selection_from_patch_scroll(self, scroll_y: float) -> None:
        if not self.show_full_patch or not self._outline_offsets:
            return
        patch_scroll = self._full_patch_scroll()
        viewport_height = (
            patch_scroll.scrollable_content_region.height
            or patch_scroll.content_region.height
            or patch_scroll.size.height
            or 1
        )
        anchor_line = max(int(scroll_y) + max(viewport_height // 2, 0), 0)
        active_index = self._outline_offsets[0][1]
        for offset, file_index in self._outline_offsets:
            if offset > anchor_line:
                break
            active_index = file_index
        if active_index == self.selected_file_index:
            self._sync_outline_cursor(active_index)
            return
        self.selected_file_index = active_index
        self._sync_outline_cursor(active_index)

    def _on_full_patch_scroll_y(self, old_value: float, new_value: float) -> None:
        if self._ignore_next_patch_scroll_sync:
            self._ignore_next_patch_scroll_sync = False
            return
        self._sync_selection_from_patch_scroll(new_value)

    def _visible_file_indices(self) -> list[int]:
        """Original indices of the files shown in the table. Narrowed by the /
        files filter, which only applies in diff mode (the full-patch outline
        stays complete so its scroll-sync is unaffected)."""
        query = self._files_query.strip().lower()
        if not query or self.show_full_patch:
            return list(range(len(self.detail.file_changes)))
        indices: list[int] = []
        for index, change in enumerate(self.detail.file_changes):
            section = self._section_for_change(index, change)
            haystack = change.path.lower()
            if section is not None and section.is_rename:
                haystack = f"{section.old_path} {section.new_path}".lower()
            if query in haystack:
                indices.append(index)
        return indices

    def _update_files_table(self) -> None:
        table = self._current_files_table()
        file_width = self._apply_files_table_layout()
        table.clear(columns=False)
        self._suspend_file_events = True
        try:
            visible = self._visible_file_indices()
            for index in visible:
                change = self.detail.file_changes[index]
                section = self._section_for_change(index, change)
                table.add_row(
                    compact_label_for_change(change, section, file_width),
                    self._build_stat_cell(change.additions, positive=True),
                    self._build_stat_cell(change.deletions, positive=False),
                    key=str(index),
                )
            if visible:
                row = visible.index(self.selected_file_index) if self.selected_file_index in visible else 0
                table.move_cursor(row=row, animate=False, scroll=True)
        finally:
            self._suspend_file_events = False

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id not in {"commit-detail-files-diff", "commit-detail-files-outline"} or self._suspend_file_events:
            return
        # While typing in the files filter, the table is repopulated and its
        # cursor moves -- ignore that highlight so it doesn't change the selection
        # or reconcile focus away from the input.
        if self._files_filter_input().has_focus:
            return
        row_key = event.row_key.value if event.row_key else None
        if row_key is None:
            return
        index = int(str(row_key))
        if index != self.selected_file_index:
            self.selected_file_index = index
            if self.show_full_patch:
                if not self._mode_transition_active:
                    self._schedule_reconcile(scroll_to_selection=True)
            else:
                self._render_patch_panel(refresh_content=True)
                self._schedule_reconcile()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id not in {"commit-detail-files-diff", "commit-detail-files-outline"} or self._suspend_file_events:
            return
        row_key = event.row_key.value if event.row_key else None
        if row_key is None:
            return
        self.selected_file_index = int(str(row_key))
        if self.show_full_patch:
            self._schedule_reconcile(
                focus_target="patch",
                scroll_to_selection=True,
            )
        else:
            self._render_patch_panel(refresh_content=True)
            self._schedule_reconcile(focus_target="patch")

    def _scroll_target(self) -> VerticalScroll | DataTable:
        return self._pane_target(self.active_pane)

    def action_focus_next_pane(self) -> None:
        panes = self._available_panes()
        if not panes:
            return
        current = self.active_pane if self.active_pane in panes else panes[0]
        index = panes.index(current)
        self.active_pane = panes[(index + 1) % len(panes)]
        self._focus_active_pane()

    def action_focus_prev_pane(self) -> None:
        panes = self._available_panes()
        if not panes:
            return
        current = self.active_pane if self.active_pane in panes else panes[0]
        index = panes.index(current)
        self.active_pane = panes[(index - 1) % len(panes)]
        self._focus_active_pane()

    def action_cursor_up(self) -> None:
        target = self._scroll_target()
        if isinstance(target, DataTable):
            target.action_cursor_up()
        else:
            target.scroll_up(animate=False)

    def action_cursor_down(self) -> None:
        target = self._scroll_target()
        if isinstance(target, DataTable):
            target.action_cursor_down()
        else:
            target.scroll_down(animate=False)

    def action_page_up(self) -> None:
        target = self._scroll_target()
        if isinstance(target, DataTable):
            target.action_page_up()
        else:
            target.scroll_page_up(animate=False)

    def action_page_down(self) -> None:
        target = self._scroll_target()
        if isinstance(target, DataTable):
            target.action_page_down()
        else:
            target.scroll_page_down(animate=False)

    def action_scroll_top(self) -> None:
        target = self._scroll_target()
        if isinstance(target, DataTable):
            if self.detail.file_changes:
                target.move_cursor(row=0, animate=False, scroll=True)
        else:
            target.scroll_home(animate=False)

    def action_scroll_bottom(self) -> None:
        target = self._scroll_target()
        if isinstance(target, DataTable):
            if self.detail.file_changes:
                target.move_cursor(
                    row=len(self.detail.file_changes) - 1,
                    animate=False,
                    scroll=True,
                )
        else:
            target.scroll_end(animate=False)

    def action_toggle_patch_scope(self) -> None:
        was_full_patch = self.show_full_patch
        self._mode_transition_active = True
        self._force_requested_focus = True
        self.show_full_patch = not self.show_full_patch
        self.active_pane = "files" if was_full_patch else "patch"
        scroll_to_selection = not was_full_patch and not self._full_patch_ready
        if not was_full_patch:
            self._full_patch_ready = True
        self._render_detail_view(
            focus_target=self.active_pane,
            scroll_to_selection=scroll_to_selection,
        )
        # the files filter only applies in diff mode; drop it when going full
        if self.show_full_patch and self._files_filter_input().display:
            files_filter = self._files_filter_input()
            files_filter.value = ""
            files_filter.display = False
            self._files_query = ""
        if self._search_input().display:
            self._search_input().placeholder = (
                "Search patch" if self.show_full_patch else "Search diff"
            )

    def action_close_screen(self) -> None:
        if self._search_input().display:
            self._close_search()
            return
        if self._files_filter_input().display:
            self._close_files_filter()
            return
        self.dismiss(None)

    def handle_repo_context_refresh(
        self,
        previous_context: RepoContext,
        new_context: RepoContext,
        previous_snapshot: RepoScanSnapshot,
        new_snapshot: RepoScanSnapshot,
    ) -> None:
        self.sub_title = self._build_repo_stats_sub_title()
        if self.source_branch is None:
            return
        old_heads = dict(previous_snapshot.branch_heads)
        new_heads = dict(new_snapshot.branch_heads)
        old_branch_head = old_heads.get(self.source_branch)
        new_branch_head = new_heads.get(self.source_branch)
        if (
            old_branch_head is None
            or new_branch_head is None
            or old_branch_head == new_branch_head
            or self.commit_hash != old_branch_head
        ):
            return
        self.commit_hash = new_branch_head
        self.detail = self.service.get_commit_detail(new_branch_head)
        self.selected_file_index = 0
        for table_id in self._files_table_signatures:
            self._files_table_signatures[table_id] = None
        self._ignore_next_patch_scroll_sync = False
        self._render_detail_view(
            focus_target="patch" if self.show_full_patch else self.DEFAULT_PANE,
            scroll_to_selection=self.show_full_patch,
        )
