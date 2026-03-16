from __future__ import annotations

import datetime as dt
import unittest
from pathlib import Path

from git_viewer_textual.app import GitViewerApp
from git_viewer_textual.data import filter_commits
from git_viewer_textual.models import CommitRecord
from git_viewer_textual.repository import build_repo_context


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
    async def test_commit_detail_blocks_main_screen_bindings(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "CommitDetailScreen")

            await pilot.press("b")
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "CommitDetailScreen")

    async def test_graph_panel_uses_real_focus(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            self.assertEqual(getattr(app.focused, "id", None), "commits-table")

            await pilot.press("shift+tab")
            await pilot.pause()
            self.assertEqual(getattr(app.focused, "id", None), "graph-content")

            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "MainScreen")


class FilterTests(unittest.TestCase):
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
