"""Commit detail modal screen."""

from __future__ import annotations

import datetime as dt

from rich.table import Table
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.color import Color
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Static

from ..data import display_label_for_change
from ..models import CommitFileChange, CommitPatchSection
from ..repository import RepoService
from ..theme import make_style, resolve_theme_palette
from ..utils import rel_time, truncate_middle, utc_now


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

    #commit-detail-files {
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

    #commit-detail-scroll {
        height: 1fr;
        overflow-y: auto;
        scrollbar-gutter: stable;
    }

    #commit-detail-content {
        height: auto;
        min-height: 1;
    }

    DataTable {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("escape", "close_screen", "Back", group=BACK_GROUP),
        Binding("tab", "focus_next_pane", "Next Pane", priority=True, group=PANE_GROUP),
        Binding(
            "shift+tab",
            "focus_prev_pane",
            "Prev Pane",
            priority=True,
            key_display="S-Tab",
            group=PANE_GROUP,
        ),
        Binding("up", "cursor_up", "Up", priority=True, group=MOVE_GROUP),
        Binding("down", "cursor_down", "Down", priority=True, group=MOVE_GROUP),
        Binding("pageup", "page_up", "Page Up", priority=True, group=PAGE_GROUP),
        Binding("pagedown", "page_down", "Page Down", priority=True, group=PAGE_GROUP),
        Binding("home", "scroll_top", "Top", priority=True, group=JUMP_GROUP),
        Binding("end", "scroll_bottom", "Bottom", priority=True, group=JUMP_GROUP),
        Binding("p", "toggle_patch_scope", "Patch"),
    ]

    def __init__(self, service: RepoService, commit_hash: str) -> None:
        super().__init__()
        self.service = service
        self.commit_hash = commit_hash
        self.detail = service.get_commit_detail(commit_hash)
        self.show_full_patch = False
        self.selected_file_index = 0
        self._suspend_file_events = False
        self._patch_section_offsets: dict[CommitPatchSection, int] = {}

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
                    yield DataTable(id="commit-detail-files")
            with Vertical(id="commit-detail-main"):
                with Vertical(
                    id="commit-detail-patch-panel",
                    classes="commit-detail-panel",
                ):
                    yield Static(id="commit-detail-patch-title", classes="commit-detail-panel-title")
                    with VerticalScroll(id="commit-detail-scroll"):
                        yield Static(id="commit-detail-content")
        yield Footer()

    def on_mount(self) -> None:
        self.watch(self.app, "theme", self._on_theme_change, init=False)
        files_table = self.query_one("#commit-detail-files", DataTable)
        files_table.cursor_type = "row"
        files_table.show_horizontal_scrollbar = False
        files_table.add_column("File", width=18, key="file")
        files_table.add_column("+", width=4, key="adds")
        files_table.add_column("-", width=4, key="dels")
        self._apply_theme()
        self._render_detail_view()
        files_table.focus()

    def _on_theme_change(self, old_value: str, new_value: str) -> None:
        self._apply_theme()
        self._render_detail_view()

    def on_resize(self, event: events.Resize) -> None:
        if self.is_mounted:
            self._render_detail_view()

    def _focus_targets(self) -> tuple[VerticalScroll | DataTable, ...]:
        if self.show_full_patch:
            return (
                self.query_one("#commit-detail-files", DataTable),
                self.query_one("#commit-detail-scroll", VerticalScroll),
            )
        return (
            self.query_one("#commit-detail-overview-scroll", VerticalScroll),
            self.query_one("#commit-detail-files", DataTable),
            self.query_one("#commit-detail-scroll", VerticalScroll),
        )

    def _current_pane(self) -> str:
        focused_id = getattr(self.focused, "id", None)
        return {
            "commit-detail-overview-scroll": "overview",
            "commit-detail-files": "files",
            "commit-detail-scroll": "patch",
        }.get(focused_id, "patch" if self.show_full_patch else "files")

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
        active_pane = self._current_pane()
        panel_ids = {
            "overview": "#commit-detail-overview-panel",
            "files": "#commit-detail-files-panel",
            "patch": "#commit-detail-patch-panel",
        }
        for pane, selector in panel_ids.items():
            panel = self.query_one(selector, Vertical)
            border_color = palette.primary if pane == active_pane else palette.border
            panel.styles.border = ("solid", border_color)

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        self._refresh_focus_styles()

    def _apply_detail_layout(self) -> None:
        sidebar = self.query_one("#commit-detail-sidebar", Vertical)
        overview_panel = self.query_one("#commit-detail-overview-panel", Vertical)
        files_panel = self.query_one("#commit-detail-files-panel", Vertical)

        if self.show_full_patch:
            sidebar.styles.width = 28
            overview_panel.styles.display = "none"
            files_panel.styles.height = "1fr"
        else:
            sidebar.styles.width = 36
            overview_panel.styles.display = "block"
            files_panel.styles.height = "1fr"

    def _ensure_valid_focus(self, *, prefer_patch: bool = False) -> None:
        focused = self.focused
        targets = self._focus_targets()
        if focused in targets:
            return
        fallback = (
            self.query_one("#commit-detail-scroll", VerticalScroll)
            if prefer_patch
            else targets[0]
        )
        fallback.focus()

    def _render_detail_view(self) -> None:
        if self.detail.file_changes and not (
            0 <= self.selected_file_index < len(self.detail.file_changes)
        ):
            self.selected_file_index = 0
        self._apply_detail_layout()

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

        self._configure_files_table()
        self._update_files_table()
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
        if self.show_full_patch:
            files_meta.append("  ", style=make_style(palette.muted))
            files_meta.append(
                "Select to jump",
                style=make_style(palette.muted),
            )
        else:
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
        self.query_one("#commit-detail-files-meta", Static).update(files_meta)

        self._render_patch_panel(refresh_content=True, follow_selection=self.show_full_patch)
        self._ensure_valid_focus(prefer_patch=self.show_full_patch)
        self._refresh_focus_styles()
        self.call_after_refresh(self._refresh_files_table_after_layout)

    def _refresh_files_table_after_layout(self) -> None:
        self._configure_files_table()
        self._update_files_table()

    def _files_table_layout(self) -> tuple[int, int, int]:
        table = self.query_one("#commit-detail-files", DataTable)
        width = table.size.width or table.content_region.width or 28
        padding = 6
        if self.show_full_patch:
            file_width = max(8, width - padding)
            return file_width, 0, 0
        stats_width = 4
        file_width = max(8, width - (stats_width * 2) - padding)
        return file_width, stats_width, stats_width

    def _files_table_has_stats(self) -> bool:
        table = self.query_one("#commit-detail-files", DataTable)
        try:
            table.columns["adds"]
            table.columns["dels"]
        except KeyError:
            return False
        return True

    def _configure_files_table(self) -> None:
        table = self.query_one("#commit-detail-files", DataTable)
        has_stats = self._files_table_has_stats()
        if self.show_full_patch and has_stats:
            table.remove_column("adds")
            table.remove_column("dels")
        elif not self.show_full_patch and not has_stats:
            table.add_column("+", width=4, key="adds")
            table.add_column("-", width=4, key="dels")

    def _apply_files_table_layout(self) -> int:
        table = self.query_one("#commit-detail-files", DataTable)
        file_width, adds_width, dels_width = self._files_table_layout()
        table.columns["file"].width = file_width
        if self._files_table_has_stats():
            table.columns["adds"].width = adds_width
            table.columns["dels"].width = dels_width
        return file_width

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
        return truncate_middle(display_label_for_change(change, section), 56)

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
        follow_selection: bool,
    ) -> None:
        palette = resolve_theme_palette(self.app.current_theme)
        patch_sections = self._visible_patch_sections()
        self.query_one("#commit-detail-patch-title", Static).update(self._build_patch_title())

        if refresh_content:
            content = Text()
            self._patch_section_offsets = {}
            current_line = 0
            if patch_sections:
                for section_index, section in enumerate(patch_sections):
                    if self.show_full_patch:
                        self._patch_section_offsets[section] = current_line
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
                        content.append(line)
                        content.append("\n")
                        current_line += 1
                    rendered_patch = self._render_patch_text(section.patch)
                    content.append(rendered_patch)
                    current_line += self._patch_line_count(rendered_patch)
                    if section_index < len(patch_sections) - 1:
                        content.append("\n\n")
                        current_line += 2
            else:
                content.append(
                    "No diff available for this selection.",
                    style=make_style(palette.muted, bold=True),
                )
            self.query_one("#commit-detail-content", Static).update(content)

        if self.show_full_patch and follow_selection:
            self.call_after_refresh(self._scroll_patch_to_selected)

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
        self.query_one("#commit-detail-scroll", VerticalScroll).scroll_to(
            y=max(offset - 1, 0),
            animate=False,
        )

    def _update_files_table(self) -> None:
        table = self.query_one("#commit-detail-files", DataTable)
        file_width = self._apply_files_table_layout()
        show_stats = self._files_table_has_stats()
        table.clear(columns=False)
        self._suspend_file_events = True
        try:
            for index, change in enumerate(self.detail.file_changes):
                section = self._section_for_change(index, change)
                label = display_label_for_change(change, section)
                if show_stats:
                    additions = "-" if change.additions is None else str(change.additions)
                    deletions = "-" if change.deletions is None else str(change.deletions)
                    table.add_row(
                        truncate_middle(label, file_width),
                        additions,
                        deletions,
                        key=str(index),
                    )
                else:
                    table.add_row(
                        truncate_middle(label, file_width),
                        key=str(index),
                    )
            if self.detail.file_changes:
                table.move_cursor(row=self.selected_file_index, animate=False, scroll=True)
        finally:
            self._suspend_file_events = False

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id != "commit-detail-files" or self._suspend_file_events:
            return
        row_key = event.row_key.value if event.row_key else None
        if row_key is None:
            return
        index = int(str(row_key))
        if index != self.selected_file_index:
            self.selected_file_index = index
            self._render_patch_panel(
                refresh_content=not self.show_full_patch,
                follow_selection=self.show_full_patch,
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "commit-detail-files" or self._suspend_file_events:
            return
        row_key = event.row_key.value if event.row_key else None
        if row_key is None:
            return
        self.selected_file_index = int(str(row_key))
        if self.show_full_patch:
            self._render_patch_panel(refresh_content=False, follow_selection=True)
            self.query_one("#commit-detail-scroll", VerticalScroll).focus()
        else:
            self._render_patch_panel(refresh_content=True, follow_selection=False)
            self.query_one("#commit-detail-scroll", VerticalScroll).focus()

    def _scroll_target(self) -> VerticalScroll | DataTable:
        focused = self.focused
        if isinstance(focused, DataTable):
            return focused
        if isinstance(focused, VerticalScroll):
            return focused
        return self.query_one("#commit-detail-files", DataTable)

    def action_focus_next_pane(self) -> None:
        targets = self._focus_targets()
        focused = self._scroll_target()
        index = targets.index(focused) if focused in targets else 1
        targets[(index + 1) % len(targets)].focus()

    def action_focus_prev_pane(self) -> None:
        targets = self._focus_targets()
        focused = self._scroll_target()
        index = targets.index(focused) if focused in targets else 1
        targets[(index - 1) % len(targets)].focus()

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
        self.show_full_patch = not self.show_full_patch
        self._render_detail_view()
        self.query_one("#commit-detail-scroll", VerticalScroll).focus()

    def action_close_screen(self) -> None:
        self.dismiss(None)
