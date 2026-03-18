"""Commit detail modal screen."""

from __future__ import annotations

from functools import partial

from rich.table import Table
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Static

from ..data import compact_label_for_change, display_label_for_change
from ..focus import (
    mark_scroll_focus_pending,
    scroll_has_overflow,
    set_scroll_focusability,
)
from ..models import CommitFileChange, CommitPatchSection, RepoContext, RepoScanSnapshot
from ..repository import RepoService
from ..theme import make_style, resolve_theme_palette
from ..utils import rel_time, utc_now


class CommitDetailScreen(ModalScreen[None]):
    """Full-screen modal for a single commit."""

    BACK_GROUP = Binding.Group("Back", compact=True)
    PANE_GROUP = Binding.Group("Pane")
    MOVE_GROUP = Binding.Group("Move", compact=True)
    PAGE_GROUP = Binding.Group("Page", compact=True)
    JUMP_GROUP = Binding.Group("Jump", compact=True)

    CSS = """
    #commit-detail-summary-bar {
        padding: 0 1;
    }

    #commit-detail-body {
        layout: horizontal;
        height: 1fr;
        margin: 0 1 1 1;
    }

    .commit-detail-panel {
        padding: 1 1;
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
        height: 12;
        min-height: 7;
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
        margin-bottom: 1;
        height: auto;
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
        Binding("up", "cursor_up", "Up", priority=True, group=MOVE_GROUP),
        Binding("down", "cursor_down", "Down", priority=True, group=MOVE_GROUP),
        Binding("tab", "focus_next_pane", "Next Pane", priority=True, group=PANE_GROUP),
        Binding(
            "shift+tab",
            "focus_prev_pane",
            "Prev Pane",
            priority=True,
            key_display="S-Tab",
            group=PANE_GROUP,
        ),
        Binding("pageup", "page_up", "Page Up", priority=True, group=PAGE_GROUP),
        Binding("pagedown", "page_down", "Page Down", priority=True, group=PAGE_GROUP),
        Binding("home", "scroll_top", "Top", priority=True, group=JUMP_GROUP),
        Binding("end", "scroll_bottom", "Bottom", priority=True, group=JUMP_GROUP),
        Binding("p", "toggle_patch_scope", "Patch"),
        Binding("escape", "close_screen", "Back", group=BACK_GROUP),
    ]

    DEFAULT_PANE = "files"

    def __init__(
        self,
        service: RepoService,
        commit_hash: str,
        source_branch: str | None = None,
    ) -> None:
        super().__init__()
        self.service = service
        self.commit_hash = commit_hash
        self.source_branch = source_branch
        self.detail = service.get_commit_detail(commit_hash)
        self.show_full_patch = False
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

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
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
                    yield DataTable(id="commit-detail-files-diff", show_row_labels=False)
                    yield DataTable(id="commit-detail-files-outline", show_row_labels=False)
            with Vertical(id="commit-detail-main"):
                with Vertical(
                    id="commit-detail-patch-panel",
                    classes="commit-detail-panel",
                ):
                    yield Static(id="commit-detail-patch-title", classes="commit-detail-panel-title")
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
        self._apply_theme()
        self._render_detail_view(focus_target=self.DEFAULT_PANE)

    def _on_theme_change(self, old_value: str, new_value: str) -> None:
        self._apply_theme()
        self._render_detail_view()

    def on_resize(self, event: events.Resize) -> None:
        if self.is_mounted:
            self._render_detail_view()

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
        panes = ["files"]
        overview_scroll = self.query_one("#commit-detail-overview-scroll", VerticalScroll)
        patch_scroll = self._active_patch_scroll()
        if not self.show_full_patch and overview_scroll.can_focus:
            panes.insert(0, "overview")
        if patch_scroll.can_focus:
            panes.append("patch")
        return tuple(panes)

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
        self._refresh_focus_styles()
        if self.focused is not target:
            target.focus()

    def _refresh_scroll_focusability(self) -> None:
        overview_scroll = self.query_one("#commit-detail-overview-scroll", VerticalScroll)
        selected_patch_scroll = self._selected_patch_scroll()
        full_patch_scroll = self._full_patch_scroll()

        set_scroll_focusability(overview_scroll, selected_patch_scroll, full_patch_scroll)
        overview_scroll.can_focus = (
            not self.show_full_patch and scroll_has_overflow(overview_scroll)
        )
        selected_patch_scroll.can_focus = (
            not self.show_full_patch and scroll_has_overflow(selected_patch_scroll)
        )
        full_patch_scroll.can_focus = (
            self.show_full_patch and scroll_has_overflow(full_patch_scroll)
        )
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
        palette = resolve_theme_palette(self.app.current_theme)
        self.styles.background = palette.background

        summary_bar = self.query_one("#commit-detail-summary-bar", Static)
        summary_bar.styles.background = palette.surface
        summary_bar.styles.border = ("solid", palette.border)

        for panel in self.query(".commit-detail-panel"):
            panel.styles.background = palette.panel
            panel.styles.border = ("solid", palette.border)

        for title in self.query(".commit-detail-panel-title"):
            title.styles.color = palette.muted

        self._refresh_focus_styles()

    def _refresh_focus_styles(self) -> None:
        palette = resolve_theme_palette(self.app.current_theme)
        panel_ids = {
            "overview": "#commit-detail-overview-panel",
            "files": "#commit-detail-files-panel",
            "patch": "#commit-detail-patch-panel",
        }
        for pane, selector in panel_ids.items():
            panel = self.query_one(selector, Vertical)
            border_color = palette.primary if pane == self.active_pane else palette.border
            panel.styles.border = ("solid", border_color)

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        self._sync_active_pane_from_focus()
        self._refresh_focus_styles()

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
            and table.row_count == len(self.detail.file_changes)
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
        self._refresh_focus_styles()

    def _render_detail_view(
        self,
        *,
        focus_target: str | None = None,
        scroll_to_selection: bool = False,
    ) -> None:
        if self.detail.file_changes and not (
            0 <= self.selected_file_index < len(self.detail.file_changes)
        ):
            self.selected_file_index = 0
        if focus_target is not None:
            self.active_pane = focus_target
            self._refresh_focus_styles()
        self._apply_detail_layout()
        self._diff_files_table().styles.display = "none" if self.show_full_patch else "block"
        self._outline_files_table().styles.display = "block" if self.show_full_patch else "none"
        self._selected_patch_scroll().styles.display = "none" if self.show_full_patch else "block"
        self._full_patch_scroll().styles.display = "block" if self.show_full_patch else "none"
        mark_scroll_focus_pending(
            self.query_one("#commit-detail-overview-scroll", VerticalScroll),
            self._selected_patch_scroll(),
            self._full_patch_scroll(),
        )

        palette = resolve_theme_palette(self.app.current_theme)
        now = utc_now()
        total_additions = sum(change.additions or 0 for change in self.detail.file_changes)
        total_deletions = sum(change.deletions or 0 for change in self.detail.file_changes)

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
        file_word = "file" if len(self.detail.file_changes) == 1 else "files"
        files_title = (
            "Patch Outline"
            if self.show_full_patch
            else "Files Changed"
        )
        self.query_one("#commit-detail-files-title", Static).update(files_title)
        files_meta = Text()
        files_meta.append(
            f"{len(self.detail.file_changes)} {file_word}",
            style=make_style(palette.foreground, bold=True),
        )
        files_meta.append("  ", style=make_style(palette.muted))
        files_meta.append(
            f"+{total_additions}",
            style=make_style(palette.success, bold=True),
        )
        files_meta.append("  ", style=make_style(palette.muted))
        files_meta.append(
            f"-{total_deletions}",
            style=make_style(palette.error, bold=True),
        )
        if self.show_full_patch:
            files_meta.append("  ", style=make_style(palette.muted))
            files_meta.append(
                "Select to jump",
                style=make_style(palette.muted),
            )
        if self.detail.file_changes:
            selected_change = self.detail.file_changes[self.selected_file_index]
            selected_section = self._section_for_change(self.selected_file_index, selected_change)
            files_meta.append("\n", style=make_style(palette.muted))
            files_meta.append(
                display_label_for_change(selected_change, selected_section),
                style=make_style(palette.foreground),
            )
        self.query_one("#commit-detail-files-meta", Static).update(files_meta)

        self._render_patch_panel(refresh_content=True)
        self._schedule_reconcile(
            focus_target=focus_target,
            scroll_to_selection=scroll_to_selection,
            force_files_table_refresh=False,
        )

    def _files_table_layout(self, table: DataTable | None = None) -> tuple[int, int, int]:
        table = table or self._current_files_table()
        width = (
            table.scrollable_content_region.width
            or table.content_region.width
            or table.size.width
            or 28
        )
        padding = 6
        stats_width = 4
        file_width = max(8, width - (stats_width * 2) - padding)
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
        if 0 <= index < len(self.detail.patch_sections):
            candidate = self.detail.patch_sections[index]
            if candidate.path == change.path or candidate.new_path == change.path:
                return candidate
        return next(
            (
                item
                for item in self.detail.patch_sections
                if item.path == change.path or item.new_path == change.path
            ),
            None,
        )

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
        palette = resolve_theme_palette(self.app.current_theme)
        if value is None:
            return Text("-", style=make_style(palette.muted))
        return Text(
            str(value),
            style=make_style(
                palette.success if positive else palette.error,
                bold=True,
            ),
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
        palette = resolve_theme_palette(self.app.current_theme)
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
        palette = resolve_theme_palette(self.app.current_theme)
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
        palette = resolve_theme_palette(self.app.current_theme)
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
        table = self._current_files_table()
        if table.cursor_row == index:
            return
        self._suspend_file_events = True
        try:
            table.move_cursor(row=index, animate=False, scroll=True)
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

    def _update_files_table(self) -> None:
        table = self._current_files_table()
        file_width = self._apply_files_table_layout()
        table.clear(columns=False)
        self._suspend_file_events = True
        try:
            for index, change in enumerate(self.detail.file_changes):
                section = self._section_for_change(index, change)
                table.add_row(
                    compact_label_for_change(change, section, file_width),
                    self._build_stat_cell(change.additions, positive=True),
                    self._build_stat_cell(change.deletions, positive=False),
                    key=str(index),
                )
            if self.detail.file_changes:
                table.move_cursor(row=self.selected_file_index, animate=False, scroll=True)
        finally:
            self._suspend_file_events = False

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id not in {"commit-detail-files-diff", "commit-detail-files-outline"} or self._suspend_file_events:
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

    def action_close_screen(self) -> None:
        self.dismiss(None)

    def handle_repo_context_refresh(
        self,
        previous_context: RepoContext,
        new_context: RepoContext,
        previous_snapshot: RepoScanSnapshot,
        new_snapshot: RepoScanSnapshot,
    ) -> None:
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
