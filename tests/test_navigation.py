from __future__ import annotations

import collections
import datetime as dt
import io
import unittest
from pathlib import Path

from rich.console import Console

from git_viewer_textual.app import GitViewerApp
from git_viewer_textual.data import filter_commits, resolve_graph_window
from git_viewer_textual.models import CommitRecord
from git_viewer_textual.render import (
    build_language_bar,
    build_language_summary,
    build_language_title,
    render_contribution_graph,
)
from git_viewer_textual.repository import build_repo_context
from git_viewer_textual.theme import resolve_theme_palette


def make_app() -> GitViewerApp:
    repo_dir = Path(__file__).resolve().parents[2] / "example-repo"
    context, service = build_repo_context(
        repo_dir=str(repo_dir),
        language_mode="bytes",
        exclude_languages=set(),
        extra_ignored_dirs=set(),
    )
    return GitViewerApp(context, service)


class NavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_main_screen_uses_header_subtitle_instead_of_extra_summary_bar(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            self.assertEqual(type(app.screen).__name__, "MainScreen")
            self.assertEqual(len(app.screen.query("#summary-bar")), 0)
            self.assertTrue(app.screen.sub_title)
            self.assertIn("branches", app.screen.sub_title)
            self.assertIn("commits", app.screen.sub_title)
            self.assertIn("scanned files", app.screen.sub_title)
            self.assertNotIn("languages", app.screen.sub_title)
            self.assertNotIn("all", app.screen.sub_title)

    async def test_commit_detail_blocks_main_screen_bindings(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "CommitDetailScreen")

            await pilot.press("b")
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "CommitDetailScreen")

    async def test_graph_panel_is_not_in_focus_cycle(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            self.assertEqual(getattr(app.focused, "id", None), "commits-table")

            await pilot.press("shift+tab")
            await pilot.pause()
            self.assertEqual(getattr(app.focused, "id", None), "language-summary-scroll")

            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "MainScreen")

    async def test_graph_title_shows_commit_count_and_language_panel_shows_language_count(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            graph_title_render = app.screen.query_one("#graph-title").render()
            language_title_render = app.screen.query_one("#language-title").render()
            graph_title = getattr(graph_title_render, "_renderable", graph_title_render)
            language_title = getattr(language_title_render, "_renderable", language_title_render)

            console = Console(file=io.StringIO(), record=True, width=140)
            console.print(graph_title)
            graph_title_text = console.export_text()
            self.assertIn("commits", graph_title_text)

            language_console = Console(file=io.StringIO(), record=True, width=140)
            language_console.print(language_title)
            language_title_text = language_console.export_text()
            self.assertIn("Languages", language_title_text)
            self.assertRegex(language_title_text, r"\b\d+\b")

    async def test_languages_panel_uses_summary_widget_instead_of_duplicate_list(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            self.assertEqual(len(app.screen.query("#language-bar")), 1)
            self.assertEqual(len(app.screen.query("#language-list")), 0)
            self.assertEqual(len(app.screen.query("#language-summary")), 1)
            self.assertEqual(len(app.screen.query("#language-summary-scroll")), 1)
            self.assertEqual(len(app.screen.query("#language-meta")), 0)
            self.assertEqual(len(app.screen.query("#language-details")), 0)
            self.assertEqual(len(app.screen.query("#language-details-scroll")), 0)
            self.assertEqual(app.screen.query_one("#language-summary-scroll").max_scroll_y, 0)
            await pilot.press("tab")
            await pilot.press("tab")
            await pilot.pause()
            self.assertEqual(getattr(app.focused, "id", None), "language-summary-scroll")


class FilterTests(unittest.TestCase):
    def test_build_language_title_stays_compact_but_keeps_top_language_when_width_allows(self) -> None:
        app = make_app()
        title = build_language_title(
            app.context.language_stats,
            resolve_theme_palette(app.current_theme),
            width=44,
        )
        console = Console(file=io.StringIO(), record=True, width=140)
        console.print(title)
        title_text = console.export_text()
        self.assertIn("Languages", title_text)
        self.assertIn("Top", title_text)
        self.assertIn("Markdown", title_text)
        self.assertIn("%", title_text)

    def test_build_language_summary_renders_all_languages_without_placeholder(self) -> None:
        app = make_app()
        summary = build_language_summary(
            app.context.language_stats,
            selected_language=str(app.context.language_stats[0]["language"]),
            palette=resolve_theme_palette(app.current_theme),
            width=44,
        )
        console = Console(file=io.StringIO(), record=True, width=80)
        console.print(summary)
        summary_text = console.export_text()
        self.assertIn("JavaScript", summary_text)
        self.assertIn("Other", summary_text)
        self.assertNotIn("more languages", summary_text)

    def test_build_language_bar_respects_requested_width(self) -> None:
        app = make_app()
        bar = build_language_bar(
            app.context.language_stats,
            resolve_theme_palette(app.current_theme),
            width=20,
        )
        console = Console(file=io.StringIO(), record=True, width=20)
        console.print(bar)
        bar_text = console.export_text().strip("\n")
        self.assertEqual(len(bar_text.splitlines()), 1)
        self.assertEqual(len(bar_text), 20)

    def test_resolve_graph_window_uses_short_window_for_current_year(self) -> None:
        start, end = resolve_graph_window(
            "30",
            2026,
            now=dt.datetime(2026, 3, 16, 12, 0, 0),
        )
        self.assertEqual(start, dt.date(2026, 2, 15))
        self.assertEqual(end, dt.date(2026, 3, 16))

        old_start, old_end = resolve_graph_window(
            "30",
            2025,
            now=dt.datetime(2026, 3, 16, 12, 0, 0),
        )
        self.assertEqual(old_start, dt.date(2025, 1, 1))
        self.assertEqual(old_end, dt.date(2025, 12, 31))

    def test_render_contribution_graph_uses_single_cell_selection_in_short_window(self) -> None:
        app = make_app()
        graph = render_contribution_graph(
            collections.Counter(
                {
                    dt.date(2026, 3, 12): 1,
                    dt.date(2026, 3, 15): 3,
                }
            ),
            dt.date(2026, 3, 10),
            dt.date(2026, 3, 16),
            dt.date(2026, 3, 15),
            resolve_theme_palette(app.current_theme),
        )
        console = Console(file=io.StringIO(), record=True, width=160)
        console.print(graph)
        graph_text = console.export_text()
        self.assertIn("Mar", graph_text)
        self.assertNotIn("▣", graph_text)
        self.assertIn("Sun ■■", graph_text)

    def test_filter_commits_uses_current_time_window(self) -> None:
        now = dt.datetime(2026, 3, 16, 12, 0, 0)
        recent = CommitRecord(
            commit_hash="a" * 40,
            authored_at=now - dt.timedelta(days=3),
            message="recent",
            author_name="A",
            author_email="a@example.com",
            changed_files=("src/app.py",),
        )
        old = CommitRecord(
            commit_hash="b" * 40,
            authored_at=now - dt.timedelta(days=40),
            message="old",
            author_name="B",
            author_email="b@example.com",
            changed_files=("src/old.py",),
        )

        filtered = filter_commits([recent, old], "7", None, now=now)
        self.assertEqual([item.commit_hash for item in filtered], [recent.commit_hash])
