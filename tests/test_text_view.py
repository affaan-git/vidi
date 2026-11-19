from __future__ import annotations

import contextlib
import os
import pathlib
import subprocess
import tempfile
import time
import unittest
from collections.abc import Iterator

from rich.style import Style
from textual.app import App, ComposeResult

from vidi import config
from vidi.app import VidiApp
from vidi.repository import build_repo_context
from vidi.screens.commit_detail import CommitDetailScreen
from vidi.screens.file_view import FileViewScreen
from vidi.text_view import TextView


def _git(repo: str, *args: str) -> None:
    subprocess.run(
        ["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
        check=True,
        capture_output=True,
    )


def _commit(repo: str, files: dict[str, str], message: str) -> None:
    for path, content in files.items():
        with open(os.path.join(repo, path), "w") as handle:
            handle.write(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


@contextlib.contextmanager
def _repo_app(commits: list[dict[str, str]]) -> Iterator[tuple[VidiApp, list[str]]]:
    """An app over a fresh repo, with the commit hashes oldest first."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = os.path.join(tmp, "repo")
        os.makedirs(repo)
        _git(repo, "init", "-q", "-b", "main")
        for index, files in enumerate(commits):
            _commit(repo, files, f"commit {index}")
        context, service = build_repo_context(
            repo_dir=repo, language_mode="bytes", exclude_languages=set(), extra_ignored_dirs=set()
        )
        hashes = [c.commit_hash for c in service.get_branch_data(context.current_branch).commits]
        yield VidiApp(context, service), hashes[::-1]


@contextlib.contextmanager
def _temp_config() -> Iterator[None]:
    orig_dir, orig_path = config.CONFIG_DIR, config.CONFIG_PATH
    try:
        with tempfile.TemporaryDirectory() as tmp:
            config.CONFIG_DIR = pathlib.Path(tmp)
            config.CONFIG_PATH = config.CONFIG_DIR / "config.json"
            yield
    finally:
        config.CONFIG_DIR, config.CONFIG_PATH = orig_dir, orig_path


def _rows(view: TextView) -> list[str]:
    return [view.render_line(y).text for y in range(view.scrollable_content_region.height)]


class _ViewApp(App[None]):
    def __init__(self, view: TextView) -> None:
        super().__init__()
        self.view = view

    def compose(self) -> ComposeResult:
        yield self.view


class TextViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_wrap_mode_maps_lines_to_rows(self) -> None:
        view = TextView(wrap=True)
        async with _ViewApp(view).run_test(size=(32, 12)) as pilot:
            width = view.scrollable_content_region.width
            self.assertGreater(width, 4)
            lines = ["a" * (2 * width + 3), "b", "\u65e5" * (width // 2 * 2), ""]
            view.set_lines(lines, Style())
            await pilot.pause()
            # 3 rows for the long line, 1 for "b", 2 for the wide line, 1 for the empty line
            self.assertEqual(view.virtual_size.height, 7)
            self.assertEqual([view.row_for_line(i) for i in range(4)], [0, 3, 4, 6])
            self.assertEqual([view.line_at_row(r) for r in range(7)], [0, 0, 0, 1, 2, 2, 3])
            rows = _rows(view)
            self.assertEqual(rows[0], "a" * width)
            self.assertEqual(rows[2], "aaa".ljust(width))
            self.assertEqual(rows[3], "b".ljust(width))
            self.assertEqual(rows[4].rstrip(), "\u65e5" * (width // 2))

    async def test_wrap_rows_follow_the_width(self) -> None:
        view = TextView(wrap=True)
        async with _ViewApp(view).run_test(size=(42, 12)) as pilot:
            view.set_lines(["x" * 200, "y"], Style())
            await pilot.pause()
            wide = view.row_for_line(1)
            await pilot.resize_terminal(22, 12)
            await pilot.pause()
            self.assertGreater(view.row_for_line(1), wide)
            width = view.scrollable_content_region.width
            self.assertEqual(view.row_for_line(1), -(-200 // width))

    async def test_no_wrap_mode_scrolls_sideways_and_keeps_the_gutter(self) -> None:
        view = TextView(wrap=False, line_numbers=True)
        async with _ViewApp(view).run_test(size=(40, 10)) as pilot:
            view.set_lines(["0123456789" * 20, "short"], Style())
            await pilot.pause()
            self.assertEqual(view.virtual_size.height, 2)
            self.assertGreater(view.max_scroll_x, 0)
            self.assertTrue(_rows(view)[0].startswith("1  0123"))
            view.scroll_to(x=5, animate=False)
            await pilot.pause()
            self.assertTrue(_rows(view)[0].startswith("1  5678"))
            self.assertEqual(_rows(view)[1].rstrip(), "2")

    async def test_switching_wrap_keeps_the_top_line(self) -> None:
        view = TextView(wrap=False)
        async with _ViewApp(view).run_test(size=(30, 8)) as pilot:
            view.set_lines(["z" * 100] * 50, Style())
            await pilot.pause()
            view.scroll_to(y=20, animate=False)
            await pilot.pause()
            view.wrap = True
            await pilot.pause()
            self.assertEqual(view.line_at_row(int(view.scroll_y)), 20)
            self.assertGreater(view.scroll_y, 20)

    async def test_highlights_follow_wrapped_rows(self) -> None:
        view = TextView(wrap=True)
        match, current = Style(bgcolor="blue"), Style(bgcolor="red")
        async with _ViewApp(view).run_test(size=(32, 8)) as pilot:
            width = view.scrollable_content_region.width
            view.set_match_styles(match, current)
            view.set_lines(["a" * (width - 2) + "needle", "needle"], Style())
            view.search("needle")
            await pilot.pause()

            def styled(row: int, color: str) -> str:
                return "".join(
                    seg.text
                    for seg in view.render_line(row)
                    if seg.style is not None and seg.style.bgcolor is not None
                    and seg.style.bgcolor.name == color
                )

            self.assertEqual(styled(0, "red"), "ne")
            self.assertEqual(styled(1, "red"), "edle")
            self.assertEqual(styled(2, "blue"), "needle")
            view.step_match(1)
            self.assertEqual(styled(0, "blue"), "ne")
            self.assertEqual(styled(2, "red"), "needle")

    async def test_search_narrows_and_steps(self) -> None:
        view = TextView()
        async with _ViewApp(view).run_test(size=(40, 10)) as pilot:
            view.set_lines(["alpha", "Beta", "gamma", "alphabet"], Style())
            view.search("a")
            self.assertEqual(view.matches, [0, 1, 2, 3])
            view.search("AL")
            self.assertEqual(view.matches, [0, 3])
            self.assertEqual(view.current_match, 0)
            view.step_match(1)
            self.assertEqual(view.current_match, 1)
            view.step_match(1)
            self.assertEqual(view.current_match, 0)
            view.search("bet")
            self.assertEqual(view.matches, [1, 3])
            view.set_lines(["bet", "none"], Style())
            self.assertEqual(view.matches, [0])
            await pilot.pause()


class FileViewWrapTests(unittest.IsolatedAsyncioTestCase):
    async def test_file_view_search_scrolls_sideways_to_the_match(self) -> None:
        content = "start\n" + "x" * 300 + " needle\n" + "end\n"
        with _temp_config(), _repo_app([{"a.txt": content}]) as (app, hashes):
            async with app.run_test(size=(80, 24)) as pilot:
                app.push_screen(FileViewScreen(app.service, hashes[0], ["a.txt"], 0))
                await pilot.pause()
                view = app.screen._scroll()
                self.assertFalse(view.wrap)
                self.assertEqual(view.scroll_x, 0)
                await pilot.press("slash", "n", "e", "e", "d")
                await pilot.pause()
                self.assertEqual(app.screen._match_lines, [1])
                self.assertGreater(view.scroll_x, 200)
                self.assertTrue(any("needle" in row for row in _rows(view)))

                # left / right scroll sideways once the search box is closed
                await pilot.press("escape", "home")
                await pilot.pause()
                self.assertEqual(view.scroll_x, 0)
                await pilot.press("right", "right")
                await pilot.pause()
                self.assertGreater(view.scroll_x, 0)
                await pilot.press("left", "left")
                await pilot.pause()
                self.assertEqual(view.scroll_x, 0)

    async def test_left_and_right_edit_the_search_box(self) -> None:
        content = "y" * 300 + "\n"
        with _temp_config(), _repo_app([{"a.txt": content}]) as (app, hashes):
            async with app.run_test(size=(80, 24)) as pilot:
                app.push_screen(FileViewScreen(app.service, hashes[0], ["a.txt"], 0))
                await pilot.pause()
                await pilot.press("slash", "a", "c", "left", "b")
                await pilot.pause()
                self.assertEqual(app.screen._search_input().value, "abc")
                self.assertEqual(app.screen._scroll().scroll_x, 0)

    def test_wrap_settings_roundtrip(self) -> None:
        with _temp_config():
            self.assertFalse(config.load_wrap_file_view())
            self.assertTrue(config.load_wrap_diffs())
            config.save_wrap_file_view(True)
            config.save_wrap_diffs(False)
            self.assertTrue(config.load_wrap_file_view())
            self.assertFalse(config.load_wrap_diffs())

    async def test_wrap_commands_toggle_open_views_and_persist(self) -> None:
        with _temp_config(), _repo_app([{"a.txt": "a\n"}, {"a.txt": "b\n"}]) as (app, hashes):
            async with app.run_test(size=(120, 30)) as pilot:
                app.push_screen(CommitDetailScreen(app.service, hashes[1]))
                await pilot.pause()
                detail = app.screen
                app.push_screen(FileViewScreen(app.service, hashes[1], ["a.txt"], 0))
                await pilot.pause()
                titles = [command.title for command in app.get_system_commands(app.screen)]
                self.assertIn("Wrap lines in file view", titles)
                self.assertIn("Stop wrapping lines in diffs", titles)
                self.assertFalse(app.screen._scroll().wrap)
                self.assertTrue(detail._full_patch_scroll().wrap)

                app._command_toggle_wrap_file_view()
                app._command_toggle_wrap_diffs()
                await pilot.pause()
                self.assertTrue(app.screen._scroll().wrap)
                self.assertFalse(detail._full_patch_scroll().wrap)
                self.assertFalse(detail._selected_patch_scroll().wrap)
                self.assertTrue(config.load_wrap_file_view())
                self.assertFalse(config.load_wrap_diffs())
                titles = [command.title for command in app.get_system_commands(app.screen)]
                self.assertIn("Stop wrapping lines in file view", titles)
                self.assertIn("Wrap lines in diffs", titles)


class PatchWrapTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_patch_outline_follows_wrapped_rows(self) -> None:
        long_lines = "".join(f"{i} " + "w" * 150 + "\n" for i in range(20))
        files = {"a.txt": long_lines, "b.txt": "short\n" * 5}
        with _temp_config(), _repo_app([{"base.txt": "x\n"}, files]) as (app, hashes):
            async with app.run_test(size=(100, 30)) as pilot:
                app.push_screen(CommitDetailScreen(app.service, hashes[1]))
                await pilot.pause()
                screen = app.screen
                await pilot.press("p")
                await pilot.pause()
                await pilot.pause()
                view = screen._full_patch_scroll()
                self.assertTrue(view.wrap)
                (first, _), (second, second_index) = screen._outline_offsets
                self.assertEqual(second_index, 1)
                second_row = view.row_for_line(second)
                # wrapping puts the second file's header far below its line index
                self.assertGreater(second_row, second + 20)

                view.scroll_end(animate=False)
                await pilot.pause()
                self.assertEqual(screen.selected_file_index, 1)

                view.scroll_home(animate=False)
                await pilot.pause()
                self.assertEqual(screen.selected_file_index, 0)

                # Choosing a file in the outline jumps to its header row.
                await pilot.press("shift+tab", "down", "enter")
                await pilot.pause()
                await pilot.pause()
                self.assertEqual(screen.selected_file_index, 1)
                self.assertEqual(int(view.scroll_y), min(second_row, view.max_scroll_y))


class PerformanceGuardTests(unittest.IsolatedAsyncioTestCase):
    """Large files open and search quickly. The bounds are far above the expected times."""

    BOUND_SECONDS = 3.0

    @staticmethod
    def _big_file() -> str:
        line = "    result = handle(request, value, index)  # error data return\n"
        return line * (2 * 1024 * 1024 // len(line))

    async def test_large_file_view_opens_and_searches_quickly(self) -> None:
        with _temp_config(), _repo_app([{"big.py": self._big_file()}]) as (app, hashes):
            async with app.run_test(size=(140, 40)) as pilot:
                started = time.perf_counter()
                app.push_screen(FileViewScreen(app.service, hashes[0], ["big.py"], 0))
                await pilot.pause()
                self.assertLess(time.perf_counter() - started, self.BOUND_SECONDS)
                await pilot.press("slash")
                started = time.perf_counter()
                await pilot.press("e")
                await pilot.pause()
                self.assertLess(time.perf_counter() - started, self.BOUND_SECONDS)
                self.assertGreater(len(app.screen._match_lines), 10000)

    async def test_large_commit_diff_opens_and_searches_quickly(self) -> None:
        with _temp_config(), _repo_app([{"a.txt": "a\n"}, {"big.py": self._big_file()}]) as (
            app,
            hashes,
        ):
            async with app.run_test(size=(140, 40)) as pilot:
                started = time.perf_counter()
                app.push_screen(CommitDetailScreen(app.service, hashes[1]))
                await pilot.pause()
                self.assertLess(time.perf_counter() - started, self.BOUND_SECONDS)
                await pilot.press("tab")
                await pilot.pause()
                self.assertEqual(app.focused.id, "commit-detail-selected-scroll")
                await pilot.press("slash")
                started = time.perf_counter()
                await pilot.press("e")
                await pilot.pause()
                self.assertLess(time.perf_counter() - started, self.BOUND_SECONDS)
                self.assertGreater(len(app.screen._match_lines), 10000)
