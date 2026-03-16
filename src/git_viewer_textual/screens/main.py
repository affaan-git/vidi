"""Main application screen."""

from __future__ import annotations

import collections
import datetime as dt

from rich.table import Table
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from ..data import RANGE_PRESETS, filter_commits
from ..models import BranchData, CommitRecord, RepoContext
from ..render import (
    build_graph_title,
    build_language_summary,
    build_language_title,
    render_contribution_graph,
)
from ..repository import RepoService
from ..theme import make_style, resolve_theme_palette
from ..utils import rel_time, truncate_middle, utc_now
from ..widgets import FocusableStatic
from .branch_picker import BranchPickerScreen
from .commit_detail import CommitDetailScreen


class MainScreen(Screen[None]):
    """Primary analytics screen."""

    CSS = """
    #body {
        layout: horizontal;
        height: 1fr;
        margin: 1 1 1 1;
    }

    .left-column {
        width: 2fr;
        margin-right: 1;
        height: 1fr;
    }

    .right-column {
        width: 1fr;
        height: 1fr;
    }

    .panel {
        margin-bottom: 0;
        padding: 1 1;
    }

    .panel-title {
        text-style: bold;
        margin-bottom: 0;
    }

    #graph-panel,
    #details-panel {
        margin-bottom: 1;
    }

    #graph-panel {
        height: auto;
        min-height: 12;
    }

    #graph-content {
        height: auto;
        min-height: 8;
    }

    #details-panel {
        height: 2fr;
        min-height: 10;
    }

    #day-details-scroll {
        height: 1fr;
        overflow-y: auto;
        scrollbar-gutter: stable;
    }

    #day-scroll-hint {
        height: 1;
        content-align: right middle;
    }

    #commits-panel {
        height: 1fr;
        min-height: 10;
    }

    #languages-panel {
        height: 1fr;
        min-height: 12;
    }

    #language-title {
        margin-bottom: 1;
    }

    #language-summary {
        height: auto;
        min-height: 4;
    }

    #day-details {
        height: auto;
        min-height: 1;
    }

    DataTable {
        height: 1fr;
    }
    """

    BINDINGS = [
        ("b", "open_branch_picker", "Branches"),
        ("[", "prev_year", "Prev Year"),
        ("]", "next_year", "Next Year"),
        ("r", "cycle_range", "Cycle Range"),
        Binding("tab", "focus_next_panel", "Next Panel", priority=True),
        Binding("shift+tab", "focus_prev_panel", "Prev Panel", priority=True),
        Binding("up", "cursor_up", "Up", priority=True),
        Binding("down", "cursor_down", "Down", priority=True),
        ("j", "day_down", "Next Day"),
        ("k", "day_up", "Prev Day"),
    ]

    selected_branch: reactive[str]
    selected_range: reactive[str]
    selected_year: reactive[int]
    selected_day: reactive[dt.date | None]
    selected_language: reactive[str]
    selected_commit_hash: reactive[str | None]

    def __init__(self, context: RepoContext, service: RepoService) -> None:
        super().__init__()
        self.context = context
        self.service = service
        self.selected_branch = context.current_branch
        self.selected_range = "all"
        initial_branch = service.get_branch_data(context.current_branch)
        self.selected_year = (
            max(initial_branch.years) if initial_branch.years else dt.date.today().year
        )
        self.selected_day = None
        self.selected_language = (
            str(context.language_stats[0]["language"])
            if context.language_stats
            else "Other"
        )
        self.selected_commit_hash = None
        self._theme_palette = resolve_theme_palette(self.app.current_theme)
        self._language_keys: list[str] = [
            str(item["language"]) for item in context.language_stats
        ]
        self._day_keys: list[dt.date] = []
        self._suspend_table_events = False
        self._mounted = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            with Vertical(classes="left-column"):
                with Vertical(id="graph-panel", classes="panel"):
                    yield Static("Contribution Activity", id="graph-title", classes="panel-title")
                    yield FocusableStatic(id="graph-content")
                with Vertical(id="commits-panel", classes="panel"):
                    yield Static("Year Commits", classes="panel-title")
                    yield DataTable(id="commits-table")
            with Vertical(classes="right-column"):
                with Vertical(id="details-panel", classes="panel"):
                    yield Static("Commit Details", id="details-title", classes="panel-title")
                    with VerticalScroll(id="day-details-scroll"):
                        yield Static(id="day-details")
                    yield Static(id="day-scroll-hint")
                with Vertical(id="languages-panel", classes="panel"):
                    yield Static(id="language-title", classes="panel-title")
                    yield FocusableStatic(id="language-summary")
        yield Footer()

    def on_mount(self) -> None:
        self.watch(self.app, "theme", self._on_theme_change, init=False)

        commits_table = self.query_one("#commits-table", DataTable)
        commits_table.cursor_type = "row"
        commits_table.show_horizontal_scrollbar = False
        commits_table.add_column("Hash", width=8)
        commits_table.add_column("Message", width=28)
        commits_table.add_column("Author", width=12)
        commits_table.add_column("When", width=7)
        commits_table.add_column("Files", width=5)

        self._apply_theme()
        self._refresh_year_options()
        self.sub_title = self._build_repo_stats_sub_title()
        self.refresh_view()
        commits_table.focus()
        self._mounted = True
        self._refresh_panel_styles()

    def _focus_targets(self) -> tuple[FocusableStatic | DataTable | VerticalScroll, ...]:
        return (
            self.query_one("#graph-content", FocusableStatic),
            self.query_one("#commits-table", DataTable),
            self.query_one("#day-details-scroll", VerticalScroll),
            self.query_one("#language-summary", FocusableStatic),
        )

    def _current_panel(self) -> str:
        focused_id = getattr(self.focused, "id", None)
        return {
            "graph-content": "graph",
            "commits-table": "commits",
            "day-details-scroll": "details",
            "language-summary": "languages",
        }.get(focused_id, "commits")

    def _on_theme_change(self, old_value: str, new_value: str) -> None:
        self._apply_theme()
        branch_data, commits, counts = self._current_view_state(normalize_selection=False)
        self._render_view(commits, counts, include_table=False, reset_scroll=False)

    def on_resize(self, event: events.Resize) -> None:
        if not self._mounted:
            return
        branch_data, commits, counts = self._current_view_state(normalize_selection=False)
        self._render_view(commits, counts, include_table=False, reset_scroll=False)

    def _apply_theme(self) -> None:
        palette = resolve_theme_palette(self.app.current_theme)
        self._theme_palette = palette
        self.styles.background = palette.background

        for panel in self.query(".panel"):
            panel.styles.background = palette.panel
            panel.styles.border = ("solid", palette.border)

        for title in self.query(".panel-title"):
            title.styles.color = palette.muted

        self.query_one("#day-scroll-hint", Static).styles.color = palette.muted
        self._refresh_panel_styles()

    def _refresh_panel_styles(self) -> None:
        palette = self._theme_palette
        current_panel = self._current_panel()
        panel_ids = {
            "graph": "#graph-panel",
            "commits": "#commits-panel",
            "details": "#details-panel",
            "languages": "#languages-panel",
        }
        for panel_name, selector in panel_ids.items():
            panel = self.query_one(selector, Vertical)
            border_color = palette.primary if panel_name == current_panel else palette.border
            panel.styles.border = ("solid", border_color)

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        self._refresh_panel_styles()

    def _refresh_year_options(self) -> None:
        branch_data = self.service.get_branch_data(self.selected_branch)
        commits = filter_commits(branch_data.commits, self.selected_range, None)
        years = sorted({commit.authored_date.year for commit in commits}) or branch_data.years
        if self.selected_year not in years and years:
            self.selected_year = max(years)

    def _filtered_branch_data(self) -> tuple[BranchData, list[CommitRecord]]:
        branch_data = self.service.get_branch_data(self.selected_branch)
        commits = filter_commits(
            branch_data.commits,
            self.selected_range,
            self.selected_year,
        )
        return branch_data, commits

    def _current_view_state(
        self,
        *,
        normalize_selection: bool,
    ) -> tuple[BranchData, list[CommitRecord], collections.Counter[dt.date]]:
        branch_data, commits = self._filtered_branch_data()
        counts: collections.Counter[dt.date] = collections.Counter(
            commit.authored_date for commit in commits
        )
        if normalize_selection:
            if self.selected_day is None or self.selected_day not in counts:
                self.selected_day = max(counts) if counts else None
            if self.selected_commit_hash not in {commit.commit_hash for commit in commits}:
                day_commits = [
                    commit
                    for commit in commits
                    if self.selected_day and commit.authored_date == self.selected_day
                ]
                self.selected_commit_hash = (
                    day_commits[0].commit_hash
                    if day_commits
                    else (commits[0].commit_hash if commits else None)
                )
        return branch_data, commits, counts

    def refresh_view(self) -> None:
        self._suspend_table_events = True
        branch_data, commits, counts = self._current_view_state(normalize_selection=True)
        self._render_view(commits, counts, include_table=True, reset_scroll=True)
        self._suspend_table_events = False

    def _render_view(
        self,
        commits: list[CommitRecord],
        counts: collections.Counter[dt.date],
        *,
        include_table: bool,
        reset_scroll: bool,
    ) -> None:
        self.query_one("#graph-title", Static).update(
            build_graph_title(
                self.selected_branch,
                self.selected_range,
                self.selected_year,
                len(commits),
                self._theme_palette,
            )
        )
        if include_table:
            self._update_commits_table(commits)
        self._day_keys = sorted(counts.keys(), reverse=True)
        self.query_one("#graph-content", FocusableStatic).update(
            render_contribution_graph(
                counts,
                self.selected_year,
                self.selected_day,
                self._theme_palette,
            )
        )
        self._update_day_details(commits, reset_scroll=reset_scroll)
        self._refresh_language_panel()

    def _set_selected_commit(
        self,
        commits: list[CommitRecord],
        commit_hash: str | None,
        *,
        sync_table: bool,
    ) -> None:
        self.selected_commit_hash = commit_hash
        selected = next(
            (commit for commit in commits if commit.commit_hash == self.selected_commit_hash),
            None,
        )
        if selected is not None:
            self.selected_day = selected.authored_date
        counts: collections.Counter[dt.date] = collections.Counter(
            commit.authored_date for commit in commits
        )
        if sync_table and self.selected_commit_hash:
            visible_hashes = [commit.commit_hash for commit in commits[:30]]
            if self.selected_commit_hash in visible_hashes:
                table = self.query_one("#commits-table", DataTable)
                row_index = visible_hashes.index(self.selected_commit_hash)
                self._suspend_table_events = True
                try:
                    table.move_cursor(row=row_index, animate=False, scroll=True)
                except Exception:
                    pass
                finally:
                    self._suspend_table_events = False
        self.query_one("#graph-content", FocusableStatic).update(
            render_contribution_graph(
                counts,
                self.selected_year,
                self.selected_day,
                self._theme_palette,
            )
        )
        self._update_day_details(commits, reset_scroll=True)

    def _update_commits_table(self, commits: list[CommitRecord]) -> None:
        table = self.query_one("#commits-table", DataTable)
        table.scroll_home(immediate=True, x_axis=True, y_axis=True)
        table.clear(columns=False)
        now = utc_now()
        for commit in commits[:30]:
            table.add_row(
                commit.commit_hash[:7],
                truncate_middle(commit.message, 28),
                truncate_middle(commit.author_name, 12),
                rel_time(commit.authored_at, now),
                str(len(commit.changed_files)),
                key=commit.commit_hash,
            )
        if self.selected_commit_hash in {commit.commit_hash for commit in commits[:30]}:
            try:
                row_index = [commit.commit_hash for commit in commits[:30]].index(
                    self.selected_commit_hash
                )
                table.move_cursor(row=row_index, animate=False, scroll=True)
            except Exception:
                pass

    def _update_day_details(self, commits: list[CommitRecord], *, reset_scroll: bool) -> None:
        palette = self._theme_palette
        details = Text()
        if self.selected_day is None:
            self.query_one("#details-title", Static).update("Commit Details")
            details.append(
                "No active day in current selection.",
                style=make_style(palette.muted, bold=True),
            )
            self.query_one("#day-details", Static).update(details)
            self.query_one("#day-scroll-hint", Static).update("")
            if reset_scroll:
                self.query_one("#day-details-scroll", VerticalScroll).scroll_home(
                    animate=False
                )
            return

        day_commits = [commit for commit in commits if commit.authored_date == self.selected_day]
        selected_commit = next(
            (commit for commit in day_commits if commit.commit_hash == self.selected_commit_hash),
            day_commits[0] if day_commits else None,
        )
        if selected_commit is None:
            self.query_one("#details-title", Static).update("Commit Details")
            details.append("No commit selected.", style=make_style(palette.muted))
            self.query_one("#day-details", Static).update(details)
            self.query_one("#day-scroll-hint", Static).update("")
            if reset_scroll:
                self.query_one("#day-details-scroll", VerticalScroll).scroll_home(
                    animate=False
                )
            return

        title_row = Table.grid(expand=True)
        title_row.add_column(ratio=1)
        title_row.add_column(justify="right", width=7)
        title_row.add_row(
            Text("Commit Details", style=make_style(palette.muted, bold=True)),
            Text(selected_commit.commit_hash[:7], style=make_style(palette.muted)),
        )
        self.query_one("#details-title", Static).update(title_row)

        details.append(
            selected_commit.authored_date.isoformat(),
            style=make_style(palette.muted),
        )
        details.append("  ", style=make_style(palette.muted))
        details.append(
            truncate_middle(selected_commit.author_name, 20),
            style=make_style(palette.foreground, bold=True),
        )
        details.append("\n")
        details.append(
            f"{selected_commit.message}\n",
            style=make_style(palette.foreground),
        )
        details.append("\nFiles changed\n", style=make_style(palette.muted, bold=True))
        for index, path in enumerate(selected_commit.changed_files):
            suffix = "\n" if index < len(selected_commit.changed_files) - 1 else ""
            details.append(f"  - {path}{suffix}", style=make_style(palette.muted))
        self.query_one("#day-details", Static).update(details)
        overflow_hint = (
            "Scroll for more"
            if len(selected_commit.changed_files) > 4 or len(selected_commit.message) > 54
            else ""
        )
        self.query_one("#day-scroll-hint", Static).update(overflow_hint)
        if reset_scroll:
            self.query_one("#day-details-scroll", VerticalScroll).scroll_home(
                animate=False
            )

    def _refresh_language_panel(self) -> None:
        panel = self.query_one("#languages-panel", Vertical)
        panel_width = panel.content_region.width or panel.size.width or 40
        title_widget = self.query_one("#language-title", Static)
        title_widget.update(
            build_language_title(
                self.context.language_stats,
                self._theme_palette,
                width=max(18, panel_width - 2),
            )
        )
        summary_widget = self.query_one("#language-summary", FocusableStatic)
        summary_width = (
            summary_widget.content_region.width
            or max(18, panel_width - 2)
        )
        panel_height = panel.content_region.height or panel.size.height or 8
        max_legend_lines = max(3, panel_height - 4)
        self.query_one("#language-summary", Static).update(
            build_language_summary(
                self.context.language_stats,
                self.selected_language,
                self._theme_palette,
                width=summary_width,
                max_legend_lines=max_legend_lines,
            )
        )

    def _build_repo_stats_sub_title(self) -> str:
        total_files = sum(len(files) for files in self.context.files_by_language.values())
        return (
            f"{self.context.repo_commit_count} total commits · "
            f"{len(self.context.branches)} branches · "
            f"{total_files} scanned files"
        )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if self._suspend_table_events or event.data_table.id != "commits-table":
            return
        row_key = event.row_key.value if event.row_key else None
        if row_key:
            branch_data, commits = self._filtered_branch_data()
            commit_hash = str(row_key)
            self._set_selected_commit(commits, commit_hash, sync_table=False)
            self.app.push_screen(CommitDetailScreen(self.service, commit_hash))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if self._suspend_table_events or event.data_table.id != "commits-table":
            return
        row_key = event.row_key.value if event.row_key else None
        if row_key and str(row_key) != self.selected_commit_hash:
            branch_data, commits = self._filtered_branch_data()
            self._set_selected_commit(commits, str(row_key), sync_table=False)

    def action_focus_next_panel(self) -> None:
        targets = self._focus_targets()
        focused = self.focused
        index = targets.index(focused) if focused in targets else 1
        targets[(index + 1) % len(targets)].focus()

    def action_focus_prev_panel(self) -> None:
        targets = self._focus_targets()
        focused = self.focused
        index = targets.index(focused) if focused in targets else 1
        targets[(index - 1) % len(targets)].focus()

    def action_cursor_up(self) -> None:
        focused = self.focused
        if isinstance(focused, DataTable):
            focused.action_cursor_up()
        elif isinstance(focused, VerticalScroll):
            focused.scroll_up(animate=False)
        elif getattr(focused, "id", None) == "language-summary":
            self.action_prev_language()
        else:
            self.action_day_up()

    def action_cursor_down(self) -> None:
        focused = self.focused
        if isinstance(focused, DataTable):
            focused.action_cursor_down()
        elif isinstance(focused, VerticalScroll):
            focused.scroll_down(animate=False)
        elif getattr(focused, "id", None) == "language-summary":
            self.action_next_language()
        else:
            self.action_day_down()

    def action_prev_year(self) -> None:
        branch_data = self.service.get_branch_data(self.selected_branch)
        years = sorted(branch_data.years)
        if self.selected_year in years:
            idx = years.index(self.selected_year)
            if idx > 0:
                self.selected_year = years[idx - 1]
                self.refresh_view()

    def action_next_year(self) -> None:
        branch_data = self.service.get_branch_data(self.selected_branch)
        years = sorted(branch_data.years)
        if self.selected_year in years:
            idx = years.index(self.selected_year)
            if idx < len(years) - 1:
                self.selected_year = years[idx + 1]
                self.refresh_view()

    def action_cycle_range(self) -> None:
        values = [value for _label, value in RANGE_PRESETS]
        idx = values.index(self.selected_range)
        self.selected_range = values[(idx + 1) % len(values)]
        self._refresh_year_options()
        self.refresh_view()

    def action_open_branch_picker(self) -> None:
        self.app.push_screen(
            BranchPickerScreen(self.context.branches, self.selected_branch),
            self._handle_branch_selected,
        )

    def _handle_branch_selected(self, branch: str | None) -> None:
        if not branch or branch == self.selected_branch:
            return
        self.selected_branch = branch
        self.selected_day = None
        self.selected_commit_hash = None
        self._refresh_year_options()
        self.refresh_view()

    def action_day_down(self) -> None:
        if not self._day_keys:
            return
        if self.selected_day not in self._day_keys:
            next_day = self._day_keys[0]
        else:
            idx = self._day_keys.index(self.selected_day)
            if idx < len(self._day_keys) - 1:
                next_day = self._day_keys[idx + 1]
            else:
                return
        branch_data, commits = self._filtered_branch_data()
        day_commits = [commit for commit in commits if commit.authored_date == next_day]
        self._set_selected_commit(
            commits,
            day_commits[0].commit_hash if day_commits else None,
            sync_table=True,
        )

    def action_day_up(self) -> None:
        if not self._day_keys:
            return
        if self.selected_day not in self._day_keys:
            next_day = self._day_keys[0]
        else:
            idx = self._day_keys.index(self.selected_day)
            if idx > 0:
                next_day = self._day_keys[idx - 1]
            else:
                return
        branch_data, commits = self._filtered_branch_data()
        day_commits = [commit for commit in commits if commit.authored_date == next_day]
        self._set_selected_commit(
            commits,
            day_commits[0].commit_hash if day_commits else None,
            sync_table=True,
        )

    def action_next_language(self) -> None:
        if not self._language_keys:
            return
        index = self._language_keys.index(self.selected_language)
        self.selected_language = self._language_keys[(index + 1) % len(self._language_keys)]
        self._refresh_language_panel()

    def action_prev_language(self) -> None:
        if not self._language_keys:
            return
        index = self._language_keys.index(self.selected_language)
        self.selected_language = self._language_keys[(index - 1) % len(self._language_keys)]
        self._refresh_language_panel()
