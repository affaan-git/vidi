"""Keys, palette commands, and background refresh across the screens."""

from __future__ import annotations

import io
import os
import tempfile
import unittest

from rich.console import Console
from textual.widgets import Button, DataTable, Input, OptionList

from vidi import config
from vidi.app import VidiApp
from vidi.screens.branch_picker import BranchPickerScreen
from vidi.screens.commit_detail import CommitDetailScreen
from vidi.screens.file_view import FileViewScreen
from vidi.screens.language_settings import LanguageSettingsScreen
from vidi.screens.main import GraphView, MainScreen
from vidi.theme import APP_THEME_NAME

from sample_repo import commit_files, git, load_repo, new_repo, sample_repo, temp_config


def _shown(widget) -> str:
    """The plain text a Static widget shows."""
    rendered = widget.render()
    console = Console(file=io.StringIO(), record=True, width=200)
    console.print(getattr(rendered, "_renderable", rendered))
    return console.export_text()


def _commit_on(repo: str, day: str, message: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_DATE": f"{day}T12:00:00+00:00"}
    git(repo, "commit", "-q", "--allow-empty", "-m", message, env=env)


def _table_messages(screen: MainScreen) -> list[str]:
    table = screen.query_one("#commits-table", DataTable)
    return [table.get_row_at(row)[1].plain for row in range(table.row_count)]


class BackgroundRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_new_commit_and_a_deleted_branch_reach_the_open_screen(self) -> None:
        with temp_config(), tempfile.TemporaryDirectory() as tmp:
            repo = new_repo(tmp)
            _commit_on(repo, "2025-06-01", "first")
            git(repo, "branch", "side")
            app = VidiApp(*load_repo(repo))
            async with app.run_test(size=(160, 45)) as pilot:
                await pilot.pause()
                screen = app.screen
                screen._handle_branch_selected("side")
                await pilot.pause()
                await pilot.pause()
                self.assertEqual(screen.selected_branch, "side")

                git(repo, "checkout", "-q", "main")
                _commit_on(repo, "2025-06-02", "second")
                git(repo, "branch", "-D", "side")
                await app._scan_repo_state()
                await pilot.pause()
                await pilot.pause()
                self.assertEqual(screen.selected_branch, "main")
                self.assertEqual(_table_messages(screen), ["second", "first"])
                self.assertIn("2 commits", screen.sub_title)
                self.assertIn("1 branch", screen.sub_title)

    async def test_new_commits_on_the_shown_branch_appear_without_a_loading_gap(self) -> None:
        with temp_config(), tempfile.TemporaryDirectory() as tmp:
            repo = new_repo(tmp)
            _commit_on(repo, "2025-06-01", "first")
            app = VidiApp(*load_repo(repo))
            async with app.run_test(size=(160, 45)) as pilot:
                await pilot.pause()
                screen = app.screen
                _commit_on(repo, "2025-06-02", "second")
                git(repo, "branch", "later")
                scan = app._scan_repo_state()
                await scan
                # The old rows stay while the new history loads.
                self.assertIn("first", _table_messages(screen))
                await pilot.pause()
                await pilot.pause()
                self.assertEqual(_table_messages(screen), ["second", "first"])

    async def test_an_unchanged_repo_keeps_the_cached_history(self) -> None:
        with temp_config():
            app = VidiApp(*load_repo(sample_repo()))
            async with app.run_test() as pilot:
                await pilot.pause()
                await app._scan_repo_state()
                cached = app.service.get_branch_data.cache_info().currsize
                await app._scan_repo_state()
                self.assertEqual(app.service.get_branch_data.cache_info().currsize, cached)
                self.assertGreater(cached, 0)

    async def test_a_scan_in_flight_defers_the_next_one(self) -> None:
        with temp_config():
            app = VidiApp(*load_repo(sample_repo()))
            async with app.run_test() as pilot:
                await pilot.pause()
                app._repo_scan_in_flight = True
                app._trigger_repo_scan()
                self.assertIsNone(app._repo_scan_task)
                app._repo_scan_in_flight = False
                app._trigger_repo_scan()
                self.assertIsNotNone(app._repo_scan_task)
                await app._repo_scan_task

    def test_scan_interval_follows_the_scan_duration_within_bounds(self) -> None:
        app = VidiApp(*load_repo(sample_repo()))
        self.assertEqual(app._next_repo_scan_delay(0.01), app.REPO_SCAN_MIN_SECONDS)
        self.assertEqual(app._next_repo_scan_delay(1.0), 30.0)
        self.assertEqual(app._next_repo_scan_delay(60.0), app.REPO_SCAN_MAX_SECONDS)


class MainScreenKeyTests(unittest.IsolatedAsyncioTestCase):
    async def test_r_cycles_the_range_and_an_empty_range_says_so(self) -> None:
        with temp_config():
            app = VidiApp(*load_repo(sample_repo()))
            async with app.run_test(size=(160, 45)) as pilot:
                await pilot.pause()
                screen = app.screen
                labels = []
                for _ in range(5):
                    await pilot.press("r")
                    await pilot.pause()
                    labels.append(screen.selected_range)
                self.assertEqual(labels, ["365", "90", "30", "7", "all"])
                self.assertIn("main · All time ·", _shown(screen.query_one("#graph-title")))
                # The sample history is years old and the last 7 days hold no commits.
                for _ in range(4):
                    await pilot.press("r")
                await pilot.pause()
                self.assertEqual(screen.selected_range, "7")
                self.assertIn("main · 7 days · 2026", _shown(screen.query_one("#graph-title")))
                self.assertIn(
                    "No active day in current selection.", _shown(screen.query_one("#day-details"))
                )

    async def test_enter_on_the_graph_hands_the_day_to_the_commit_table(self) -> None:
        with temp_config(), tempfile.TemporaryDirectory() as tmp:
            repo = new_repo(tmp)
            _commit_on(repo, "2025-06-01", "first")
            _commit_on(repo, "2025-06-03", "third")
            app = VidiApp(*load_repo(repo))
            async with app.run_test(size=(160, 45)) as pilot:
                await pilot.pause()
                screen = app.screen
                screen.query_one(GraphView).focus()
                await pilot.press("up", "up", "enter")
                await pilot.pause()
                table = screen.query_one("#commits-table", DataTable)
                self.assertIs(app.focused, table)
                self.assertEqual(table.get_row_at(table.cursor_row)[1].plain, "first")

    async def test_graph_browse_setting_waits_for_enter(self) -> None:
        with temp_config(), tempfile.TemporaryDirectory() as tmp:
            repo = new_repo(tmp)
            _commit_on(repo, "2025-06-01", "first")
            _commit_on(repo, "2025-06-03", "third")
            app = VidiApp(*load_repo(repo))
            async with app.run_test(size=(160, 45)) as pilot:
                await pilot.pause()
                app._command_toggle_graph_browse()
                self.assertTrue(config.load_graph_browse())
                screen = app.screen
                before = screen.selected_commit_hash
                screen.query_one(GraphView).focus()
                await pilot.press("up", "up")
                await pilot.pause()
                self.assertEqual(screen.selected_commit_hash, before)
                await pilot.press("enter")
                await pilot.pause()
                table = screen.query_one("#commits-table", DataTable)
                self.assertEqual(table.get_row_at(table.cursor_row)[1].plain, "first")
                app._command_toggle_graph_browse()
                self.assertFalse(config.load_graph_browse())

    async def test_clicking_a_commit_row_opens_it(self) -> None:
        with temp_config(), tempfile.TemporaryDirectory() as tmp:
            repo = new_repo(tmp)
            _commit_on(repo, "2025-06-01", "first")
            _commit_on(repo, "2025-06-03", "third")
            app = VidiApp(*load_repo(repo))
            async with app.run_test(size=(160, 45)) as pilot:
                await pilot.pause()
                table = app.screen.query_one("#commits-table", DataTable)
                # The header takes row 0 and the older commit is the second row.
                await pilot.click(table, offset=(2, 2))
                await pilot.click(table, offset=(2, 2))
                await pilot.pause()
                self.assertIsInstance(app.screen, CommitDetailScreen)
                self.assertEqual(app.screen.detail.subject, "first")


class PaletteCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_theme_changes_are_saved_and_reset(self) -> None:
        with temp_config():
            app = VidiApp(*load_repo(sample_repo()))
            async with app.run_test() as pilot:
                await pilot.pause()
                app.theme = "pro-black"
                await pilot.pause()
                self.assertEqual(config.load_theme(), "pro-black")
                app._command_reset_theme()
                await pilot.pause()
                self.assertEqual(app.theme, APP_THEME_NAME)
                self.assertEqual(config.load_theme(), APP_THEME_NAME)

    async def test_transparent_background_toggles_and_persists(self) -> None:
        with temp_config():
            app = VidiApp(*load_repo(sample_repo()))
            async with app.run_test() as pilot:
                await pilot.pause()
                app._command_toggle_transparent()
                await pilot.pause()
                self.assertTrue(config.load_transparent_background())
                self.assertTrue(app.ansi_color)
                self.assertEqual(app.current_theme.variables.get("background"), "ansi_default")
                titles = [command.title for command in app.get_system_commands(app.screen)]
                self.assertIn("Opaque background", titles)
                app._command_toggle_transparent()
                await pilot.pause()
                self.assertFalse(config.load_transparent_background())
                self.assertFalse(app.ansi_color)
                self.assertNotIn("background", app.current_theme.variables)


class BranchPickerTests(unittest.IsolatedAsyncioTestCase):
    async def test_choosing_from_the_list_switches_the_main_screen(self) -> None:
        with temp_config():
            app = VidiApp(*load_repo(sample_repo()))
            async with app.run_test(size=(160, 45)) as pilot:
                await pilot.pause()
                main = app.screen
                await pilot.press("b")
                await pilot.pause()
                picker = app.screen
                self.assertIsInstance(picker, BranchPickerScreen)
                options = picker.query_one("#branch-picker-options", OptionList)
                start = options.highlighted
                await pilot.press("up")
                self.assertEqual(options.highlighted, start - 1)
                await pilot.press("down")
                self.assertEqual(options.highlighted, start)
                options.focus()
                index = picker.filtered_branches.index("dev")
                options.highlighted = index
                await pilot.press("enter")
                await pilot.pause()
                await pilot.pause()
                await pilot.pause()
                self.assertIs(app.screen, main)
                self.assertEqual(main.selected_branch, "dev")
                self.assertIn("Newest Added Empty Commit", _table_messages(main))

    async def test_no_match_shows_a_message_and_enter_does_nothing(self) -> None:
        with temp_config():
            app = VidiApp(*load_repo(sample_repo()))
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press("b")
                await pilot.pause()
                picker = app.screen
                await pilot.press(*"zzzz")
                await pilot.pause()
                options = picker.query_one("#branch-picker-options", OptionList)
                self.assertEqual(options.option_count, 1)
                prompt = options.get_option_at_index(0).prompt
                self.assertEqual(prompt.plain, "No matching branches")
                self.assertEqual(picker.highlighted_branch, "")
                await pilot.press("enter")
                await pilot.pause()
                self.assertIs(app.screen, picker)

    async def test_escape_cancels_without_switching(self) -> None:
        with temp_config():
            app = VidiApp(*load_repo(sample_repo()))
            async with app.run_test() as pilot:
                await pilot.pause()
                main = app.screen
                branch = main.selected_branch
                await pilot.press("b", "d", "e", "v", "escape")
                await pilot.pause()
                self.assertIs(app.screen, main)
                self.assertEqual(main.selected_branch, branch)


class CommitDetailKeyTests(unittest.IsolatedAsyncioTestCase):
    async def _open(self, pilot, app: VidiApp, commit_hash: str) -> CommitDetailScreen:
        app.push_screen(CommitDetailScreen(app.service, commit_hash))
        await pilot.pause()
        await pilot.pause()
        return app.screen

    async def test_enter_in_the_files_filter_shows_the_file_and_p_drops_the_filter(self) -> None:
        files = {"alpha.txt": "a\n", "beta.txt": "b\n", "gamma.txt": "c\n"}
        with temp_config(), tempfile.TemporaryDirectory() as tmp:
            repo = new_repo(tmp)
            commit_files(repo, files, "three files")
            app = VidiApp(*load_repo(repo))
            async with app.run_test(size=(160, 45)) as pilot:
                await pilot.pause()
                head = app.service.get_branch_data("main").commits[0].commit_hash
                screen = await self._open(pilot, app, head)
                screen._current_files_table().focus()
                await pilot.press("slash", *"gam", "enter")
                await pilot.pause()
                await pilot.pause()
                selected = screen.detail.file_changes[screen.selected_file_index]
                self.assertEqual(selected.path, "gamma.txt")
                self.assertIs(app.focused, screen._current_files_table())
                filter_input = screen.query_one("#commit-detail-files-filter", Input)
                self.assertTrue(filter_input.display)
                await pilot.press("p")
                await pilot.pause()
                self.assertTrue(screen.show_full_patch)
                self.assertFalse(filter_input.display)
                self.assertEqual(filter_input.value, "")

    async def test_page_and_jump_keys_move_in_the_files_table_and_the_patch(self) -> None:
        files = {f"file{index:02}.txt": "line\n" * 80 for index in range(30)}
        with temp_config(), tempfile.TemporaryDirectory() as tmp:
            repo = new_repo(tmp)
            commit_files(repo, files, "many files")
            app = VidiApp(*load_repo(repo))
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.pause()
                head = app.service.get_branch_data("main").commits[0].commit_hash
                screen = await self._open(pilot, app, head)
                table = screen._current_files_table()
                table.focus()
                await pilot.press("end")
                self.assertEqual(table.cursor_row, len(files) - 1)
                await pilot.press("home")
                self.assertEqual(table.cursor_row, 0)
                await pilot.press("pagedown")
                self.assertGreater(table.cursor_row, 0)
                await pilot.press("pageup")
                self.assertEqual(table.cursor_row, 0)

                patch = screen._selected_patch_scroll()
                patch.focus()
                await pilot.pause()
                await pilot.press("pagedown")
                await pilot.pause()
                self.assertGreater(patch.scroll_y, 0)
                await pilot.press("end")
                await pilot.pause()
                self.assertEqual(patch.scroll_y, patch.max_scroll_y)
                await pilot.press("pageup", "home")
                await pilot.pause()
                self.assertEqual(patch.scroll_y, 0)


class FileViewKeyTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_binary_file_shows_a_message_and_search_finds_nothing(self) -> None:
        with temp_config(), tempfile.TemporaryDirectory() as tmp:
            repo = new_repo(tmp)
            commit_files(repo, {"image.bin": "\x00\x01binary\x00"}, "binary")
            app = VidiApp(*load_repo(repo))
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                head = app.service.get_branch_data("main").commits[0].commit_hash
                app.push_screen(FileViewScreen(app.service, head, ["image.bin"], 0))
                await pilot.pause()
                view = app.screen._scroll()
                self.assertEqual(view.lines, ["Unable to show this file (it may be binary)."])
                await pilot.press("slash", *"binary")
                await pilot.pause()
                self.assertEqual(view.matches, [])
                self.assertIn("no matches", _shown(app.screen.query_one("#file-view-title")))

    async def test_scroll_keys_move_through_the_file(self) -> None:
        content = "".join(f"line {index}\n" for index in range(200))
        with temp_config(), tempfile.TemporaryDirectory() as tmp:
            repo = new_repo(tmp)
            commit_files(repo, {"long.txt": content}, "long")
            app = VidiApp(*load_repo(repo))
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                head = app.service.get_branch_data("main").commits[0].commit_hash
                app.push_screen(FileViewScreen(app.service, head, ["long.txt"], 0))
                await pilot.pause()
                view = app.screen._scroll()
                await pilot.press("j", "j", "down")
                await pilot.pause()
                self.assertEqual(view.scroll_y, 3)
                await pilot.press("k", "up")
                await pilot.pause()
                self.assertEqual(view.scroll_y, 1)
                await pilot.press("pagedown")
                await pilot.pause()
                self.assertGreater(view.scroll_y, 10)
                await pilot.press("end")
                await pilot.pause()
                self.assertEqual(view.scroll_y, view.max_scroll_y)
                await pilot.press("pageup", "home")
                await pilot.pause()
                self.assertEqual(view.scroll_y, 0)


class LanguageSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_apply_button_saves_and_cancel_button_discards(self) -> None:
        with temp_config():
            app = VidiApp(*load_repo(sample_repo()))
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                app._command_language_settings()
                await pilot.pause()
                self.assertIsInstance(app.screen, LanguageSettingsScreen)
                app.screen.query_one("#language-ignore", Input).value = "vendor, generated"
                app.screen.query_one("#language-cancel", Button).press()
                await pilot.pause()
                self.assertNotIsInstance(app.screen, LanguageSettingsScreen)
                self.assertEqual(app.service.extra_ignored_dirs, set())

                app._command_language_settings()
                await pilot.pause()
                app.screen.query_one("#language-ignore", Input).value = "vendor, generated"
                app.screen.query_one("#language-apply", Button).press()
                await pilot.pause()
                self.assertEqual(app.service.extra_ignored_dirs, {"vendor", "generated"})
                saved = config.load_language_settings()
                self.assertEqual(saved["ignore_dirs"], ["vendor", "generated"])

    async def test_enter_in_a_field_applies_the_settings(self) -> None:
        with temp_config():
            app = VidiApp(*load_repo(sample_repo()))
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                app._command_language_settings()
                await pilot.pause()
                field = app.screen.query_one("#language-exclude", Input)
                field.focus()
                await pilot.press(*"JSON", "enter")
                await pilot.pause()
                self.assertNotIsInstance(app.screen, LanguageSettingsScreen)
                self.assertEqual(app.service.exclude_languages, {"JSON"})
