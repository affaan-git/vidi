"""Main application screen."""

from __future__ import annotations

import asyncio
import collections
import datetime as dt

from rich.table import Table
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Input, Static

from ..data import (
    RANGE_PRESETS,
    compact_label_for_change,
    filter_commits,
    resolve_graph_window,
    section_for_change,
)
from ..formatting import (
    append_file_change_summary_line,
    build_commit_identity_line,
    build_repo_stats_sub_title,
)
from ..models import BranchData, CommitRecord, LanguageStat, RepoContext
from ..render import (
    build_graph_title,
    build_language_bar,
    build_language_summary,
    build_language_title,
    graph_day_at,
    language_index_at,
    language_legend_rows,
    render_contribution_graph,
)
from ..repository import RepoService
from ..theme import make_style, resolve_theme_palette
from ..utils import rel_time, truncate_middle, utc_now
from .branch_picker import BranchPickerScreen
from .commit_detail import CommitDetailScreen
from .commit_files import CommitFilesScreen

# Commit table row cap, set to cover the commits the graph spans.
MAX_VISIBLE_COMMITS = 500
# Delay before re-rendering after a burst of resize events.
RESIZE_DEBOUNCE_SECONDS = 0.05
# Column widths, shared with the truncation budget.
COMMIT_MESSAGE_WIDTH = 28
COMMIT_AUTHOR_WIDTH = 12


class GraphView(Static):
    """Focusable contribution graph.

    Owns left/right week-stepping and Open, while the screen's up/down step
    days. Rows are weekdays and columns are weeks.
    """

    can_focus = True

    WEEK_GROUP = Binding.Group("Week", compact=True)
    BINDINGS = [
        Binding("enter", "open_selection", "Open"),
        Binding("left", "prev_week", "Prev", group=WEEK_GROUP),
        Binding("right", "next_week", "Next", group=WEEK_GROUP),
    ]

    def action_open_selection(self) -> None:
        self.screen.action_open()

    def action_prev_week(self) -> None:
        self.screen._move_selected_day(-7)

    def action_next_week(self) -> None:
        self.screen._move_selected_day(7)

    def on_click(self, event: events.Click) -> None:
        self.screen._click_graph(event.offset.x, event.offset.y)


class CommitTable(DataTable):
    """Commit table that owns the Open binding."""

    BINDINGS = [Binding("enter", "open_selection", "Open")]

    def action_open_selection(self) -> None:
        self.screen.action_open()


class DetailsScroll(VerticalScroll):
    """Commit-details panel that owns the Open binding."""

    BINDINGS = [Binding("enter", "open_selection", "Open")]

    def action_open_selection(self) -> None:
        self.screen.action_open()


class LanguageList(VerticalScroll):
    """Focusable languages panel that owns left/right legend navigation and Open."""

    LANGUAGE_GROUP = Binding.Group("Language", compact=True)
    BINDINGS = [
        Binding("enter", "open_selection", "Open"),
        Binding("left", "prev_language", "Prev", group=LANGUAGE_GROUP),
        Binding("right", "next_language", "Next", group=LANGUAGE_GROUP),
    ]

    def action_open_selection(self) -> None:
        self.screen.action_open()

    def action_prev_language(self) -> None:
        self.screen._move_language_linear(-1)

    def action_next_language(self) -> None:
        self.screen._move_language_linear(1)

    def on_click(self, event: events.Click) -> None:
        self.screen._click_language(event.offset.x, event.offset.y + int(self.scroll_y))


class MainScreen(Screen[None]):
    """Primary analytics screen."""

    MOVE_GROUP = Binding.Group("Move", compact=True)
    PANEL_GROUP = Binding.Group("Panel")
    YEAR_GROUP = Binding.Group("Year", compact=True)
    DAY_GROUP = Binding.Group("Day", compact=True)

    CSS = """
    MainScreen {
        background: $background;
    }

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
        background: $panel;
        border: solid $border;
    }

    #graph-panel:focus-within,
    #commits-panel:focus-within,
    #details-panel:focus-within,
    #languages-panel:focus-within {
        border: solid $primary;
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
        height: 3fr;
        min-height: 8;
    }

    #day-details-scroll {
        height: 1fr;
        overflow-y: auto;
        scrollbar-gutter: stable;
    }

    #commits-panel {
        height: 1fr;
        min-height: 5;
    }

    #commits-filter {
        display: none;
        margin: 0 0 1 0;
        border: solid $border;
        background: $surface;
        color: $text;
    }

    #commits-filter:focus {
        border: solid $primary;
    }

    #languages-panel {
        height: 1fr;
        min-height: 10;
    }

    #language-title {
        margin-bottom: 0;
    }

    #language-bar {
        height: 1;
        margin-bottom: 1;
    }

    #language-scroll {
        height: 1fr;
        overflow-y: auto;
    }

    #language-list {
        height: auto;
        min-height: 1;
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
        Binding("down", "cursor_down", "Down", priority=True, group=MOVE_GROUP),
        Binding("up", "cursor_up", "Up", priority=True, group=MOVE_GROUP),
        Binding("j", "nav_down", "Down", priority=True, show=False),
        Binding("k", "nav_up", "Up", priority=True, show=False),
        Binding("tab", "focus_next_panel", "Next Panel", priority=True, group=PANEL_GROUP),
        Binding(
            "shift+tab",
            "focus_prev_panel",
            "Prev Panel",
            priority=True,
            key_display="S-Tab",
            group=PANEL_GROUP,
        ),
        Binding("b", "open_branch_picker", "Branch"),
        Binding("f", "browse_files", "Files"),
        Binding("slash", "open_commit_filter", "Filter"),
        Binding("escape", "close_commit_filter", show=False),
        Binding("h", "day_down", "Older Day", group=DAY_GROUP),
        Binding("l", "day_up", "Newer Day", group=DAY_GROUP),
        Binding("[", "prev_year", "Prev Year", group=YEAR_GROUP),
        Binding("]", "next_year", "Next Year", group=YEAR_GROUP),
        Binding("r", "cycle_range", "Range"),
    ]

    selected_branch: str
    selected_range: str
    selected_year: int
    selected_day: dt.date | None
    selected_commit_hash: str | None
    scanned_file_count: int

    def __init__(self, context: RepoContext, service: RepoService) -> None:
        super().__init__()
        self.context = context
        self.service = service
        self.selected_branch = context.current_branch
        self.selected_range = "all"
        initial_branch = context.initial_branch_data
        self.selected_year = (
            max(initial_branch.years)
            if initial_branch is not None and initial_branch.years
            else dt.date.today().year
        )
        self.selected_day = None
        self.selected_commit_hash = None
        self._language_index = 0
        # Language breakdown of the highlighted commit's tree.
        self._language_stats: list[LanguageStat] = []
        self._language_files: dict[str, list[str]] = {}
        self._resize_timer = None
        self.scanned_file_count = service.scanned_file_count(context)
        self._theme_palette = resolve_theme_palette(self.app.current_theme)
        self._branch_data_cache: dict[str, BranchData] = {}
        if initial_branch is not None:
            self._branch_data_cache[initial_branch.branch] = initial_branch
        self._branch_load_tasks: dict[str, asyncio.Task[None]] = {}
        self._day_keys: list[dt.date] = []
        self._suspend_table_events = False
        self._commit_query = ""
        self._mounted = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="body"):
            with Vertical(classes="left-column"):
                with Vertical(id="graph-panel", classes="panel"):
                    yield Static("Contribution Activity", id="graph-title", classes="panel-title")
                    yield GraphView(id="graph-content")
                with Vertical(id="commits-panel", classes="panel"):
                    yield Static("Year Commits", id="commits-title", classes="panel-title")
                    yield Input(placeholder="Filter commits", id="commits-filter")
                    yield CommitTable(id="commits-table")
            with Vertical(classes="right-column"):
                with Vertical(id="details-panel", classes="panel"):
                    yield Static("Commit Details", id="details-title", classes="panel-title")
                    with DetailsScroll(id="day-details-scroll"):
                        yield Static(id="day-details")
                with Vertical(id="languages-panel", classes="panel"):
                    yield Static(id="language-title", classes="panel-title")
                    yield Static(id="language-bar")
                    with LanguageList(id="language-scroll"):
                        yield Static(id="language-list")
        yield Footer()

    def on_mount(self) -> None:
        self.watch(self.app, "theme", self._on_theme_change, init=False)

        commits_table = self.query_one("#commits-table", DataTable)
        commits_table.cursor_type = "row"
        commits_table.show_horizontal_scrollbar = False
        commits_table.add_column("Hash", width=8)
        commits_table.add_column("Message", width=COMMIT_MESSAGE_WIDTH)
        commits_table.add_column("Author", width=COMMIT_AUTHOR_WIDTH)
        commits_table.add_column("When", width=7)
        commits_table.add_column("Files", width=5)

        # The details scroll and the languages list are both Tab stops.
        self.query_one("#day-details-scroll", VerticalScroll).can_focus = True
        self.query_one("#language-scroll", VerticalScroll).can_focus = True

        self._mounted = True
        self._apply_theme()
        self._refresh_year_options()
        self.sub_title = self._build_repo_stats_sub_title()
        self.refresh_view()
        self.query_one("#commits-table", CommitTable).focus()
        self._ensure_branch_loaded(self.selected_branch)

    @property
    def active_panel(self) -> str:
        # The active panel is derived from focus.
        focused = self.focused
        if isinstance(focused, GraphView):
            return "graph"
        if isinstance(focused, LanguageList):
            return "languages"
        if isinstance(focused, DetailsScroll):
            return "details"
        return "commits"

    def _panel_target(self, panel: str) -> Static | DataTable | VerticalScroll:
        targets: dict[str, Static | DataTable | VerticalScroll] = {
            "graph": self.query_one("#graph-content", GraphView),
            "commits": self.query_one("#commits-table", DataTable),
            "details": self.query_one("#day-details-scroll", VerticalScroll),
            "languages": self.query_one("#language-scroll", VerticalScroll),
        }
        return targets[panel]

    def _on_theme_change(self, old_value: str, new_value: str) -> None:
        self._apply_theme()
        branch_data, commits, counts = self._current_view_state(normalize_selection=False)
        self._render_view(commits, counts, include_table=False, reset_scroll=False)

    def on_resize(self, event: events.Resize) -> None:
        if not self._mounted:
            return
        # Coalesce the resize burst into one re-render.
        if self._resize_timer is not None:
            self._resize_timer.stop()
        self._resize_timer = self.set_timer(RESIZE_DEBOUNCE_SECONDS, self._render_after_resize)

    def _render_after_resize(self) -> None:
        self._resize_timer = None
        if not self._mounted or not self.is_mounted:
            return
        branch_data, commits, counts = self._current_view_state(normalize_selection=False)
        self._render_view(commits, counts, include_table=False, reset_scroll=False)

    def on_unmount(self) -> None:
        for task in self._branch_load_tasks.values():
            task.cancel()
        self._branch_load_tasks.clear()

    def _apply_theme(self) -> None:
        # Everything but the muted title color comes from the theme via CSS.
        palette = resolve_theme_palette(self.app.current_theme)
        self._theme_palette = palette
        for title in self.query(".panel-title"):
            title.styles.color = palette.muted

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        # Re-render the legend, whose caret shows only while the languages panel has focus.
        self._render_language_summary()

    def _refresh_year_options(self) -> None:
        branch_data = self._get_branch_data(self.selected_branch)
        if branch_data is None:
            self._ensure_branch_loaded(self.selected_branch)
            return
        commits = filter_commits(branch_data.commits, self.selected_range, None)
        years = sorted({commit.authored_date.year for commit in commits}) or branch_data.years
        if self.selected_year not in years and years:
            self.selected_year = max(years)

    def _filtered_branch_data(self) -> tuple[BranchData, list[CommitRecord]]:
        branch_data = self._get_branch_data(self.selected_branch)
        if branch_data is None:
            self._ensure_branch_loaded(self.selected_branch)
            return BranchData(branch=self.selected_branch, commits=[], years=[]), []
        commits = filter_commits(
            branch_data.commits,
            self.selected_range,
            self.selected_year,
        )
        return branch_data, commits

    def _get_branch_data(self, branch: str) -> BranchData | None:
        return self._branch_data_cache.get(branch)

    def _ensure_branch_loaded(self, branch: str) -> None:
        if branch in self._branch_data_cache or branch in self._branch_load_tasks or not self.is_mounted:
            return
        self._branch_load_tasks[branch] = asyncio.create_task(self._load_branch_data(branch))

    async def _load_branch_data(self, branch: str) -> None:
        try:
            branch_data = await asyncio.to_thread(self.service.get_branch_data, branch)
        finally:
            self._branch_load_tasks.pop(branch, None)
        self._branch_data_cache[branch] = branch_data
        if not self.is_mounted or branch != self.selected_branch:
            return
        years = branch_data.years
        if years and self.selected_year not in years:
            self.selected_year = max(years)
        self.refresh_view()

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
        graph_start, graph_end = resolve_graph_window(self.selected_range, self.selected_year)
        self.query_one("#graph-content", Static).update(
            render_contribution_graph(
                counts,
                graph_start,
                graph_end,
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
            visible_hashes = [commit.commit_hash for commit in commits[:MAX_VISIBLE_COMMITS]]
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
        graph_start, graph_end = resolve_graph_window(self.selected_range, self.selected_year)
        self.query_one("#graph-content", Static).update(
            render_contribution_graph(
                counts,
                graph_start,
                graph_end,
                self.selected_day,
                self._theme_palette,
            )
        )
        self._update_day_details(commits, reset_scroll=True)
        self._refresh_language_panel()

    def _visible_commits(self, commits: list[CommitRecord]) -> list[CommitRecord]:
        """The commits shown in the table, narrowed by the / filter on message,
        author, hash, or ISO authored date."""
        query = self._commit_query.strip().lower()
        if not query:
            return commits
        return [
            commit
            for commit in commits
            if query in commit.message.lower()
            or query in commit.author_name.lower()
            or query in commit.commit_hash.lower()
            or query in commit.authored_date.isoformat()
        ]

    def _update_commits_table(self, commits: list[CommitRecord]) -> None:
        visible = self._visible_commits(commits)[:MAX_VISIBLE_COMMITS]
        self._update_commits_title(len(visible), len(commits))
        table = self.query_one("#commits-table", DataTable)
        table.scroll_home(immediate=True, x_axis=True, y_axis=True)
        table.clear(columns=False)
        now = utc_now()
        for commit in visible:
            table.add_row(
                commit.commit_hash[:7],
                Text(truncate_middle(commit.message, COMMIT_MESSAGE_WIDTH)),
                Text(truncate_middle(commit.author_name, COMMIT_AUTHOR_WIDTH)),
                rel_time(commit.authored_at, now),
                str(len(commit.changed_files)),
                key=commit.commit_hash,
            )
        visible_hashes = [commit.commit_hash for commit in visible]
        if self.selected_commit_hash in visible_hashes:
            try:
                table.move_cursor(
                    row=visible_hashes.index(self.selected_commit_hash),
                    animate=False,
                    scroll=True,
                )
            except Exception:
                pass

    def _update_commits_title(self, shown: int, total: int) -> None:
        title = "Year Commits"
        if self._commit_query.strip():
            title = f"Year Commits  ({shown} of {total})"
        self.query_one("#commits-title", Static).update(title)

    def _update_day_details(self, commits: list[CommitRecord], *, reset_scroll: bool) -> None:
        palette = self._theme_palette
        details = Text()
        if self.selected_branch not in self._branch_data_cache:
            self.query_one("#details-title", Static).update("Commit Details")
            details.append(
                f"Loading {self.selected_branch} history...",
                style=make_style(palette.muted, bold=True),
            )
            self.query_one("#day-details", Static).update(details)
            if reset_scroll:
                self.query_one("#day-details-scroll", VerticalScroll).scroll_home(
                    animate=False
                )
            return
        if self.selected_day is None:
            self.query_one("#details-title", Static).update("Commit Details")
            details.append(
                "No active day in current selection.",
                style=make_style(palette.muted, bold=True),
            )
            self.query_one("#day-details", Static).update(details)
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

        details.append_text(
            build_commit_identity_line(
                palette,
                selected_commit.authored_date.isoformat(),
                selected_commit.author_name,
                author_max_width=20,
            )
        )
        details.append("\n")
        details.append(
            f"{selected_commit.message}\n",
            style=make_style(palette.foreground),
        )
        details.append("\nFiles changed\n", style=make_style(palette.muted, bold=True))
        commit_detail = self.service.get_commit_detail(selected_commit.commit_hash)
        # Wrap against the width of the Static that renders the text, not the scroll.
        details_static = self.query_one("#day-details", Static)
        details_scroll = self.query_one("#day-details-scroll", VerticalScroll)
        content_width = details_static.content_region.width
        if not content_width:
            content_width = max(0, details_scroll.content_region.width - 2) or 56
        for index, change in enumerate(commit_detail.file_changes):
            suffix = "\n" if index < len(commit_detail.file_changes) - 1 else ""
            section = section_for_change(commit_detail.patch_sections, index, change)
            # Leave room in the path for the exact "+adds -dels" stat.
            additions_text = "+-" if change.additions is None else f"+{change.additions}"
            deletions_text = "--" if change.deletions is None else f"-{change.deletions}"
            stat_width = len(additions_text) + 1 + len(deletions_text)
            label_width = max(8, content_width - len("  - ") - 2 - stat_width)
            append_file_change_summary_line(
                details,
                palette,
                compact_label_for_change(change, section, label_width),
                change.additions,
                change.deletions,
            )
            if suffix:
                details.append(suffix, style=make_style(palette.muted))
        self.query_one("#day-details", Static).update(details)
        if reset_scroll:
            self.query_one("#day-details-scroll", VerticalScroll).scroll_home(
                animate=False
            )

    def reload_language_panel(self) -> None:
        """Re-read languages after the scan settings changed."""
        self.scanned_file_count = self.service.scanned_file_count(self.context)
        self.sub_title = self._build_repo_stats_sub_title()
        self._refresh_language_panel()

    def _refresh_language_panel(self) -> None:
        # Snapshot the highlighted commit's tree languages, cached per commit.
        if self.selected_commit_hash:
            self._language_stats, self._language_files = self.service.get_commit_language_stats(
                self.selected_commit_hash
            )
        else:
            self._language_stats, self._language_files = [], {}
        self.query_one("#language-title", Static).update(
            build_language_title(self._language_stats, self._theme_palette)
        )
        self._populate_language_list()

    def _populate_language_list(self) -> None:
        stats = self._language_stats
        if stats:
            self._language_index = max(0, min(self._language_index, len(stats) - 1))
        else:
            self._language_index = 0
        self._render_language_summary()

    def _render_language_summary(self) -> None:
        # Mark the active entry in the bar and legend only while the panel is focused.
        stats = self._language_stats
        highlighted = (
            self._language_index if stats and self.active_panel == "languages" else None
        )
        scroll = self.query_one("#language-scroll", VerticalScroll)
        width = scroll.content_region.width or scroll.size.width or 32
        self.query_one("#language-list", Static).update(
            build_language_summary(
                stats,
                self._theme_palette,
                width=width,
                highlighted_index=highlighted,
            )
        )
        bar_widget = self.query_one("#language-bar", Static)
        bar_width = bar_widget.content_region.width or bar_widget.size.width or max(8, width)
        bar_widget.update(
            build_language_bar(
                stats,
                self._theme_palette,
                width=bar_width,
                highlighted_index=highlighted,
            )
        )

    def _language_rows(self) -> list[list[int]]:
        scroll = self.query_one("#language-scroll", VerticalScroll)
        width = scroll.content_region.width or scroll.size.width or 32
        return language_legend_rows(self._language_stats, width)

    def _set_language_index(self, new_index: int) -> None:
        if new_index == self._language_index:
            return
        self._language_index = new_index
        self._render_language_summary()

    def _move_language_linear(self, delta: int) -> None:
        # left/right step through the legend in reading order.
        stats = self._language_stats
        if not stats:
            return
        self._set_language_index(max(0, min(self._language_index + delta, len(stats) - 1)))

    def _move_language_row(self, delta: int) -> None:
        # up/down move between wrapped rows at the same column, falling back to a
        # linear step when there is no row to land on.
        stats = self._language_stats
        if not stats:
            return
        rows = self._language_rows()
        row_index = next(
            (r for r, row in enumerate(rows) if self._language_index in row), 0
        )
        column = (
            rows[row_index].index(self._language_index)
            if rows and self._language_index in rows[row_index]
            else 0
        )
        target_row = row_index + delta
        if 0 <= target_row < len(rows):
            row = rows[target_row]
            self._set_language_index(row[min(column, len(row) - 1)])
        else:
            self._move_language_linear(delta)

    def _click_language(self, column: int, row: int) -> None:
        # Clicking a legend entry selects that language and focuses the panel.
        if not self._language_stats:
            return
        width = self.query_one("#language-scroll", VerticalScroll).content_region.width or 32
        index = language_index_at(self._language_stats, width, column, row)
        if index is None:
            return
        self._language_index = index
        self.query_one("#language-scroll", LanguageList).focus()
        self._render_language_summary()

    def _commit_hashes_and_index(self) -> tuple[list[str], int]:
        """The commits in view, newest-first, and the selected one's index."""
        _, commits = self._filtered_branch_data()
        hashes = [commit.commit_hash for commit in commits]
        try:
            index = hashes.index(self.selected_commit_hash)
        except ValueError:
            index = 0
        return hashes, index

    def _open_language_files(self) -> None:
        stats = self._language_stats
        if not stats or not self.selected_commit_hash:
            return
        language = str(stats[self._language_index]["language"])
        hashes, index = self._commit_hashes_and_index()
        self.app.push_screen(
            CommitFilesScreen(self.service, hashes, index, language=language)
        )

    def action_browse_files(self) -> None:
        """Browse every file in the highlighted commit's tree from any panel."""
        if not self.selected_commit_hash:
            return
        hashes, index = self._commit_hashes_and_index()
        self.app.push_screen(CommitFilesScreen(self.service, hashes, index))

    # Commit table filter

    def _commit_filter(self) -> Input:
        return self.query_one("#commits-filter", Input)

    def _commit_filter_focused(self) -> bool:
        try:
            return self._commit_filter().has_focus
        except Exception:
            return False

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # While typing in the commit filter, j/k go to the input and the arrows
        # still move rows.
        if action in ("nav_down", "nav_up") and self._commit_filter_focused():
            return False
        return True

    def action_open_commit_filter(self) -> None:
        inp = self._commit_filter()
        inp.display = True
        inp.focus()

    def action_close_commit_filter(self) -> None:
        inp = self._commit_filter()
        if not inp.display:
            return
        inp.value = ""
        inp.display = False
        self._commit_query = ""
        _, commits = self._filtered_branch_data()
        self._update_commits_table(commits)
        self.query_one("#commits-table", CommitTable).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "commits-filter":
            return
        self._commit_query = event.value
        _, commits = self._filtered_branch_data()
        self._suspend_table_events = True
        try:
            self._update_commits_table(commits)
        finally:
            self._suspend_table_events = False

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Enter keeps the filter and moves into the narrowed table.
        if event.input.id == "commits-filter":
            self.query_one("#commits-table", CommitTable).focus()

    def _build_repo_stats_sub_title(self) -> str:
        base = self.service.default_base_branch(self.context.branches)
        ahead, behind = (
            self.service.get_branch_divergence(self.selected_branch, base) if base else (0, 0)
        )
        return build_repo_stats_sub_title(
            self.context, self.scanned_file_count, self.selected_branch, ahead, behind
        )

    def handle_repo_context_refresh(
        self,
        previous_context: RepoContext,
        new_context: RepoContext,
        previous_snapshot: object,
        new_snapshot: object,
    ) -> None:
        self.context = new_context
        self.scanned_file_count = self.service.scanned_file_count(new_context)
        if self.selected_branch not in new_context.branches:
            self.selected_branch = (
                new_context.current_branch
                if new_context.current_branch in new_context.branches
                else new_context.branches[0]
            )
        stale_branches = set(self._branch_data_cache) - set(new_context.branches)
        for branch in stale_branches:
            self._branch_data_cache.pop(branch, None)
        self.sub_title = self._build_repo_stats_sub_title()
        self._refresh_year_options()
        self.refresh_view()
        self._ensure_branch_loaded(self.selected_branch)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if self._suspend_table_events or event.data_table.id != "commits-table":
            return
        row_key = event.row_key.value if event.row_key else None
        if row_key:
            branch_data, commits = self._filtered_branch_data()
            commit_hash = str(row_key)
            self._set_selected_commit(commits, commit_hash, sync_table=False)
            self.app.push_screen(
                CommitDetailScreen(
                    self.service,
                    commit_hash,
                    source_branch=self.selected_branch,
                    commit_hashes=[commit.commit_hash for commit in commits],
                )
            )

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if self._suspend_table_events or event.data_table.id != "commits-table":
            return
        row_key = event.row_key.value if event.row_key else None
        if row_key and str(row_key) != self.selected_commit_hash:
            branch_data, commits = self._filtered_branch_data()
            self._set_selected_commit(commits, str(row_key), sync_table=False)

    def action_open(self) -> None:
        if self.active_panel == "languages":
            self._open_language_files()
            return
        if self.active_panel == "graph":
            # Enter hands the graph's day off to the commit table and focuses it.
            _, commits = self._filtered_branch_data()
            day_commits = [c for c in commits if c.authored_date == self.selected_day]
            if not day_commits:
                return
            self._set_selected_commit(commits, day_commits[0].commit_hash, sync_table=True)
            self.query_one("#commits-table", CommitTable).focus()
            return
        commit_hash = self.selected_commit_hash
        if not commit_hash:
            return
        _, commits = self._filtered_branch_data()
        self.app.push_screen(
            CommitDetailScreen(
                self.service,
                commit_hash,
                source_branch=self.selected_branch,
                commit_hashes=[commit.commit_hash for commit in commits],
            )
        )

    def action_focus_next_panel(self) -> None:
        self.focus_next()

    def action_focus_prev_panel(self) -> None:
        self.focus_previous()

    def action_cursor_up(self) -> None:
        panel = self.active_panel
        if panel == "languages":
            self._move_language_row(-1)
            return
        if panel == "graph":
            self._move_selected_day(-1)
            return
        focused = self._panel_target(panel)
        if isinstance(focused, DataTable):
            focused.action_cursor_up()
        elif isinstance(focused, VerticalScroll):
            focused.scroll_up(animate=False)

    # j / k get their own actions, held back while the commit filter has focus.
    def action_nav_down(self) -> None:
        self.action_cursor_down()

    def action_nav_up(self) -> None:
        self.action_cursor_up()

    def action_cursor_down(self) -> None:
        panel = self.active_panel
        if panel == "languages":
            self._move_language_row(1)
            return
        if panel == "graph":
            self._move_selected_day(1)
            return
        focused = self._panel_target(panel)
        if isinstance(focused, DataTable):
            focused.action_cursor_down()
        elif isinstance(focused, VerticalScroll):
            focused.scroll_down(animate=False)

    def _move_selected_day(self, delta_days: int) -> None:
        # up/down move the selected day by one day and left/right by a week,
        # within the current graph window.
        graph_start, graph_end = resolve_graph_window(self.selected_range, self.selected_year)
        _, commits = self._filtered_branch_data()
        base = self.selected_day
        if base is None:
            base = self._day_keys[0] if self._day_keys else graph_end
        new_day = base + dt.timedelta(days=delta_days)
        if graph_start <= new_day <= graph_end:
            self._select_day(new_day, commits)

    def _select_day(self, day: dt.date, commits: list[CommitRecord]) -> None:
        self.selected_day = day
        if self.app.graph_browse:
            # In on-enter mode, only the square highlight moves until Enter.
            self._render_graph(commits)
            return
        day_commits = [commit for commit in commits if commit.authored_date == day]
        self._set_selected_commit(
            commits,
            day_commits[0].commit_hash if day_commits else None,
            sync_table=True,
        )

    def _click_graph(self, column: int, row: int) -> None:
        # Clicking a square selects that day like the arrows do, then focuses the graph.
        graph_start, graph_end = resolve_graph_window(self.selected_range, self.selected_year)
        day = graph_day_at(graph_start, graph_end, column, row)
        if day is None:
            return
        _, commits = self._filtered_branch_data()
        self._select_day(day, commits)
        self.query_one("#graph-content", GraphView).focus()

    def _render_graph(self, commits: list[CommitRecord]) -> None:
        counts: collections.Counter[dt.date] = collections.Counter(
            commit.authored_date for commit in commits
        )
        graph_start, graph_end = resolve_graph_window(self.selected_range, self.selected_year)
        self.query_one("#graph-content", Static).update(
            render_contribution_graph(
                counts, graph_start, graph_end, self.selected_day, self._theme_palette
            )
        )

    def action_prev_year(self) -> None:
        branch_data = self._get_branch_data(self.selected_branch)
        if branch_data is None:
            self._ensure_branch_loaded(self.selected_branch)
            return
        years = sorted(branch_data.years)
        if self.selected_year in years:
            idx = years.index(self.selected_year)
            if idx > 0:
                self.selected_year = years[idx - 1]
                self.refresh_view()

    def action_next_year(self) -> None:
        branch_data = self._get_branch_data(self.selected_branch)
        if branch_data is None:
            self._ensure_branch_loaded(self.selected_branch)
            return
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
        branches = self.context.branches
        base = self.service.default_base_branch(branches)
        divergence = (
            {branch: self.service.get_branch_divergence(branch, base) for branch in branches}
            if base
            else {}
        )
        self.app.push_screen(
            BranchPickerScreen(branches, self.selected_branch, divergence, base),
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
        self._ensure_branch_loaded(branch)

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
