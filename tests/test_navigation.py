from __future__ import annotations

import collections
import datetime as dt
import io
import unittest
from dataclasses import replace

from rich.console import Console
from rich.text import Text
from textual.color import Color
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header
from textual.widgets import Input

from git_viewer_textual.app import GitViewerApp
from git_viewer_textual.data import compact_label_for_change, filter_commits, resolve_graph_window
from git_viewer_textual.formatting import (
    append_file_change_summary_line,
    build_commit_identity_line,
)
from git_viewer_textual.models import (
    CommitFileChange,
    CommitPatchSection,
    CommitRecord,
    RepoScanSnapshot,
)
from git_viewer_textual.render import (
    build_language_bar,
    build_language_title,
    render_contribution_graph,
    resolve_heatmap_colors,
)
from git_viewer_textual.repository import build_initial_repo_context, build_repo_context
from git_viewer_textual.theme import make_change_stat_style, resolve_theme_palette

from sample_repo import sample_repo


def make_app() -> GitViewerApp:
    context, service = build_repo_context(
        repo_dir=sample_repo(),
        language_mode="bytes",
        exclude_languages=set(),
        extra_ignored_dirs=set(),
    )
    return GitViewerApp(context, service)


def make_lightweight_app() -> GitViewerApp:
    context, service = build_initial_repo_context(
        repo_dir=sample_repo(),
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
            self.assertIn("files", app.screen.sub_title)
            self.assertNotIn("languages", app.screen.sub_title)
            self.assertNotIn("all", app.screen.sub_title)
            self.assertEqual(len(app.screen.query(Header)), 1)

    async def test_main_header_uses_builtin_header_and_subtitle_file_count(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            expected_count = sum(len(files) for files in app.context.files_by_language.values())
            self.assertEqual(
                app.screen.sub_title,
                f"{app.context.repo_commit_count} commits · "
                f"{len(app.context.branches)} branches · "
                f"{expected_count} files",
            )

    def test_build_initial_repo_context_skips_eager_language_and_branch_loading(self) -> None:
        context, service = build_initial_repo_context(
            repo_dir=sample_repo(),
            language_mode="bytes",
            exclude_languages=set(),
            extra_ignored_dirs=set(),
        )

        self.assertEqual(context.language_stats, [])
        self.assertEqual(context.files_by_language, {})
        self.assertIsNone(context.initial_branch_data)
        self.assertIsNone(service.get_cached_scanned_file_count())

    async def test_lightweight_app_warms_language_data_on_mount(self) -> None:
        # The deferred context starts with no language data; on_mount must kick
        # off the warm-up scan that fills it in (a regression guard: the warm-up
        # once got detached from on_mount and languages stayed empty forever).
        app = make_lightweight_app()
        async with app.run_test() as pilot:
            task = app._initial_context_task
            if task is not None:
                await task
            await pilot.pause()
            self.assertGreater(len(app.context.language_stats), 0)
            self.assertIn("files", app.screen.sub_title)

    async def test_commit_detail_uses_same_builtin_header_without_clock(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause()
            header = app.screen.query_one(Header)
            self.assertFalse(getattr(header, "_show_clock"))
            expected_count = sum(len(files) for files in app.context.files_by_language.values())
            self.assertEqual(
                app.screen.sub_title,
                f"{app.context.repo_commit_count} commits · "
                f"{len(app.context.branches)} branches · "
                f"{expected_count} files",
            )

    async def test_main_screen_repo_refresh_updates_header_status_and_subtitle(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = app.screen
            previous_context = app.context
            new_context = replace(
                app.context,
                repo_commit_count=9,
                branches=[*app.context.branches, "release"],
                branch_heads=(*app.context.branch_heads, ("release", "1234567")),
                language_stats=[],
                files_by_language={},
            )
            app.service._cached_scanned_file_count = 0
            previous_snapshot = RepoScanSnapshot(
                branch_heads=previous_context.branch_heads,
                current_branch=previous_context.current_branch,
                repo_commit_count=previous_context.repo_commit_count,
                scanned_file_count=sum(len(files) for files in previous_context.files_by_language.values()),
            )
            new_snapshot = RepoScanSnapshot(
                branch_heads=new_context.branch_heads,
                current_branch=new_context.current_branch,
                repo_commit_count=new_context.repo_commit_count,
                scanned_file_count=0,
            )

            screen.handle_repo_context_refresh(
                previous_context,
                new_context,
                previous_snapshot,
                new_snapshot,
            )
            await pilot.pause()

            self.assertIn("9 commits", screen.sub_title)
            self.assertIn("10 branches", screen.sub_title)
            self.assertIn("0 files", screen.sub_title)

    async def test_commit_detail_blocks_main_screen_bindings(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "CommitDetailScreen")

            await pilot.press("b")
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "CommitDetailScreen")

    async def test_main_screen_tab_cycles_all_panels(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            self.assertEqual(getattr(app.focused, "id", None), "commits-table")
            self.assertEqual(app.screen.active_panel, "commits")
            self.assertTrue(app.screen.query_one("#graph-content").can_focus)
            self.assertTrue(app.screen.query_one("#day-details-scroll").can_focus)
            self.assertTrue(app.screen.query_one("#language-scroll").can_focus)

            # Tab cycles commits -> details -> languages -> graph -> commits
            await pilot.press("tab")
            await pilot.pause()
            self.assertEqual(getattr(app.focused, "id", None), "day-details-scroll")

            await pilot.press("tab")
            await pilot.pause()
            self.assertEqual(getattr(app.focused, "id", None), "language-scroll")

            await pilot.press("tab")
            await pilot.pause()
            self.assertEqual(getattr(app.focused, "id", None), "graph-content")
            self.assertEqual(app.screen.active_panel, "graph")

            await pilot.press("tab")
            await pilot.pause()
            self.assertEqual(getattr(app.focused, "id", None), "commits-table")

            await pilot.press("shift+tab")
            await pilot.pause()
            self.assertEqual(getattr(app.focused, "id", None), "graph-content")

    async def test_graph_panel_navigates_days_with_arrows(self) -> None:
        app = make_app()
        async with app.run_test(size=(160, 44)) as pilot:
            await pilot.press("shift+tab")  # commits -> graph (graph is the prior stop)
            await pilot.pause()
            self.assertEqual(getattr(app.focused, "id", None), "graph-content")
            self.assertEqual(app.screen.active_panel, "graph")
            start = app.screen.selected_day
            self.assertIsNotNone(start)

            await pilot.press("down")  # +1 day
            await pilot.pause()
            self.assertEqual((app.screen.selected_day - start).days, 1)

            after_day = app.screen.selected_day
            await pilot.press("right")  # +1 week
            await pilot.pause()
            self.assertEqual((app.screen.selected_day - after_day).days, 7)

    async def test_app_registers_all_themes(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            for name in ("git-viewer", "one-night", "pro-black"):
                self.assertIn(name, app.available_themes)
            self.assertIn(app.current_theme.name, app.available_themes)

    def test_config_theme_roundtrip(self) -> None:
        import pathlib
        import tempfile

        from git_viewer_textual import config

        orig_dir, orig_path = config.CONFIG_DIR, config.CONFIG_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                config.CONFIG_DIR = pathlib.Path(tmp)
                config.CONFIG_PATH = config.CONFIG_DIR / "config.json"
                self.assertIsNone(config.load_theme())
                config.save_theme("pro-black")
                self.assertEqual(config.load_theme(), "pro-black")
                config.reset()
                self.assertIsNone(config.load_theme())
        finally:
            config.CONFIG_DIR, config.CONFIG_PATH = orig_dir, orig_path

    async def test_help_overlay_opens_and_closes(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.press("question_mark")
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "HelpScreen")

            await pilot.press("escape")
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "MainScreen")

    async def test_main_commit_list_moves_with_j_and_k(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.press("left_square_bracket")
            await pilot.pause()
            table = app.screen.query_one("#commits-table", DataTable)
            self.assertGreaterEqual(table.row_count, 2)
            self.assertEqual(table.cursor_row, 0)

            await pilot.press("j")
            await pilot.pause()
            self.assertEqual(table.cursor_row, 1)

            await pilot.press("k")
            await pilot.pause()
            self.assertEqual(table.cursor_row, 0)

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

    async def test_main_commit_details_shows_theme_aware_file_change_stats(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            details_render = app.screen.query_one("#day-details").render()
            details = getattr(details_render, "_renderable", details_render)
            console = Console(file=io.StringIO(), record=True, width=140)
            console.print(details)
            details_text = console.export_text()
            self.assertIn("Files changed", details_text)
            self.assertRegex(details_text, r"\+\d+")
            self.assertRegex(details_text, r"-\d+")

    async def test_main_footer_uses_grouped_builtin_footer(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            footer = app.screen.query_one(Footer)
            self.assertFalse(footer.compact)
            self.assertTrue(footer.show_command_palette)
            grouped_descriptions = {
                binding.group.description
                for binding in app.screen.BINDINGS
                if getattr(binding, "group", None) is not None
            }
            self.assertEqual(grouped_descriptions, {"Move", "Panel", "Year", "Day"})
            footer_label = footer.query("FooterLabel").first()
            self.assertEqual(footer_label.styles.margin.right, 1)

    async def test_commit_detail_footer_matches_main_footer_style(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "CommitDetailScreen")

            footer = app.screen.query_one(Footer)
            self.assertFalse(footer.compact)
            grouped_descriptions = {
                binding.group.description
                for binding in app.screen.BINDINGS
                if getattr(binding, "group", None) is not None
            }
            self.assertEqual(
                grouped_descriptions,
                {"Back", "Pane", "Move", "Page", "Jump", "Commit"},
            )
            self.assertIn(
                ("q", "close_screen"),
                {
                    (binding.key, binding.action)
                    for binding in Binding.make_bindings(app.screen.BINDINGS)
                },
            )
            self.assertIn(
                ("p", "toggle_patch_scope"),
                {
                    (binding.key, binding.action)
                    for binding in Binding.make_bindings(app.screen.BINDINGS)
                },
            )

    async def test_commit_detail_full_patch_mode_reuses_files_as_outline(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "CommitDetailScreen")

            await pilot.press("p")
            await pilot.pause()
            await pilot.pause()

            self.assertTrue(app.screen.show_full_patch)
            self.assertEqual(
                app.screen.query_one("#commit-detail-overview-panel").styles.display,
                "none",
            )
            self.assertEqual(
                app.screen.query_one("#commit-detail-files-title").render(),
                "Patch Outline",
            )
            self.assertEqual(
                app.screen.query_one("#commit-detail-files-outline", DataTable).max_scroll_x,
                0,
            )
            outline_table = app.screen.query_one("#commit-detail-files-outline", DataTable)
            self.assertEqual([column.label.plain for column in outline_table.columns.values()], ["File", "+", "-"])
            first_outline_cell = outline_table.get_row_at(0)[0]
            first_outline_text = getattr(first_outline_cell, "plain", str(first_outline_cell))
            self.assertNotRegex(first_outline_text, r"\+\d+")
            self.assertNotRegex(first_outline_text, r"-\d+|--")
            first_outline_adds = outline_table.get_row_at(0)[1]
            first_outline_dels = outline_table.get_row_at(0)[2]
            self.assertEqual(getattr(first_outline_adds, "plain", str(first_outline_adds)), "2")
            self.assertEqual(getattr(first_outline_dels, "plain", str(first_outline_dels)), "0")
            self.assertEqual(getattr(app.focused, "id", None), "commit-detail-full-scroll")
            files_meta_render = app.screen.query_one("#commit-detail-files-meta").render()
            meta_console = Console(file=io.StringIO(), record=True, width=120)
            meta_console.print(files_meta_render)
            files_meta_text = meta_console.export_text()
            self.assertRegex(files_meta_text, r"\+\d+")
            self.assertRegex(files_meta_text, r"-\d+")

            await pilot.press("shift+tab")
            await pilot.pause()
            await pilot.pause()
            self.assertEqual(getattr(app.focused, "id", None), "commit-detail-files-outline")

            await pilot.press("tab")
            await pilot.pause()
            await pilot.pause()
            self.assertEqual(getattr(app.focused, "id", None), "commit-detail-full-scroll")

            await pilot.press("shift+tab")
            await pilot.pause()
            self.assertEqual(getattr(app.focused, "id", None), "commit-detail-files-outline")

            await pilot.press("down")
            await pilot.pause()
            self.assertEqual(app.screen.selected_file_index, 1)

            await pilot.press("p")
            await pilot.pause()
            await pilot.pause()
            self.assertFalse(app.screen.show_full_patch)
            self.assertEqual(
                app.screen.query_one("#commit-detail-overview-panel").styles.display,
                "block",
            )
            self.assertEqual(
                app.screen.query_one("#commit-detail-files-title").render(),
                "Files Changed",
            )
            diff_table = app.screen.query_one("#commit-detail-files-diff", DataTable)
            self.assertEqual([column.label.plain for column in diff_table.columns.values()], ["File", "+", "-"])
            self.assertEqual(getattr(app.focused, "id", None), "commit-detail-files-diff")

    async def test_commit_detail_retargets_rewritten_branch_head(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause()
            screen = app.screen
            self.assertEqual(type(screen).__name__, "CommitDetailScreen")
            old_hash = screen.commit_hash

            replacement_detail = replace(screen.detail, commit_hash="deadbeef1234", subject="amended")
            app.service.get_commit_detail = lambda commit_hash: replace(
                replacement_detail,
                commit_hash=commit_hash,
            )
            previous_snapshot = RepoScanSnapshot(
                branch_heads=(("main", old_hash),),
                current_branch="main",
                repo_commit_count=app.context.repo_commit_count,
                scanned_file_count=sum(len(files) for files in app.context.files_by_language.values()),
            )
            new_snapshot = RepoScanSnapshot(
                branch_heads=(("main", "deadbeef1234"),),
                current_branch="main",
                repo_commit_count=app.context.repo_commit_count + 1,
                scanned_file_count=sum(len(files) for files in app.context.files_by_language.values()),
            )

            screen.handle_repo_context_refresh(
                app.context,
                app.context,
                previous_snapshot,
                new_snapshot,
            )
            await pilot.pause()

            self.assertEqual(screen.commit_hash, "deadbeef1234")
            self.assertEqual(screen.detail.subject, "amended")

    async def test_commit_detail_tab_cycles_panes(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "CommitDetailScreen")
            self.assertEqual(getattr(app.focused, "id", None), "commit-detail-files-diff")
            self.assertEqual(app.screen.active_pane, "files")
            self.assertTrue(app.screen.query_one("#commit-detail-overview-scroll").can_focus)
            self.assertTrue(app.screen.query_one("#commit-detail-selected-scroll").can_focus)

            await pilot.press("tab")
            await pilot.pause()
            await pilot.pause()
            self.assertEqual(getattr(app.focused, "id", None), "commit-detail-selected-scroll")
            self.assertEqual(app.screen.active_pane, "patch")

            await pilot.press("tab")
            await pilot.pause()
            await pilot.pause()
            self.assertEqual(getattr(app.focused, "id", None), "commit-detail-overview-scroll")

    async def test_commit_detail_steps_between_commits(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.press("left_square_bracket")  # land on a year with 2+ commits
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "CommitDetailScreen")
            self.assertGreater(len(app.screen._commit_hashes), 1)
            first = app.screen.commit_hash
            # step to whichever sibling exists ( [ = older, ] = newer ), then back
            older_exists = app.screen._commit_index + 1 < len(app.screen._commit_hashes)
            step, back = ("[", "]") if older_exists else ("]", "[")

            await pilot.press(step)
            await pilot.pause()
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "CommitDetailScreen")
            self.assertNotEqual(app.screen.commit_hash, first)

            await pilot.press(back)
            await pilot.pause()
            await pilot.pause()
            self.assertEqual(app.screen.commit_hash, first)

            await pilot.press("escape")
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "MainScreen")

    async def test_commit_detail_keeps_patch_mode_when_stepping(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.press("left_square_bracket")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "CommitDetailScreen")
            self.assertFalse(app.screen.show_full_patch)
            older_exists = app.screen._commit_index + 1 < len(app.screen._commit_hashes)
            step = "[" if older_exists else "]"

            await pilot.press("p")
            await pilot.pause()
            await pilot.pause()
            self.assertTrue(app.screen.show_full_patch)

            await pilot.press(step)
            await pilot.pause()
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "CommitDetailScreen")
            self.assertTrue(app.screen.show_full_patch)  # mode carries to the new commit

    async def test_commit_detail_diff_files_table_has_no_phantom_horizontal_overflow(self) -> None:
        app = make_app()
        async with app.run_test(size=(56, 18)) as pilot:
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "CommitDetailScreen")

            table = app.screen.query_one("#commit-detail-files-diff", DataTable)
            self.assertEqual(table.max_scroll_x, 0)

    async def test_commit_detail_restores_previous_full_patch_scroll_position(self) -> None:
        app = make_app()
        async with app.run_test(size=(100, 20)) as pilot:
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "CommitDetailScreen")

            await pilot.press("p")
            await pilot.pause()
            patch_scroll = app.screen.query_one("#commit-detail-full-scroll")
            patch_scroll.scroll_end(animate=False)
            await pilot.pause()
            saved_scroll = int(patch_scroll.scroll_y)
            self.assertGreater(saved_scroll, 0)

            await pilot.press("p")
            await pilot.pause()
            self.assertFalse(app.screen.show_full_patch)

            await pilot.press("p")
            await pilot.pause()
            first_restored_scroll = int(
                app.screen.query_one("#commit-detail-full-scroll").scroll_y
            )
            await pilot.pause()
            second_restored_scroll = int(
                app.screen.query_one("#commit-detail-full-scroll").scroll_y
            )

            self.assertGreater(first_restored_scroll, 0)
            self.assertEqual(second_restored_scroll, first_restored_scroll)

    async def test_commit_detail_full_patch_scroll_updates_outline_selection(self) -> None:
        app = make_app()
        async with app.run_test(size=(100, 20)) as pilot:
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "CommitDetailScreen")

            await pilot.press("p")
            await pilot.pause()

            patch_scroll = app.screen.query_one("#commit-detail-full-scroll")
            files_table = app.screen.query_one("#commit-detail-files-outline", DataTable)
            self.assertGreater(patch_scroll.max_scroll_y, 0)
            self.assertEqual(app.screen.selected_file_index, 0)
            self.assertEqual(files_table.cursor_row, 0)

            patch_scroll.scroll_end(animate=False)
            await pilot.pause()
            self.assertEqual(app.screen.selected_file_index, 1)
            self.assertEqual(files_table.cursor_row, 1)

            await pilot.press("shift+tab")
            await pilot.pause()
            self.assertEqual(getattr(app.focused, "id", None), "commit-detail-files-outline")

            patch_scroll.scroll_home(animate=False)
            await pilot.pause()
            self.assertEqual(app.screen.selected_file_index, 0)
            self.assertEqual(files_table.cursor_row, 0)

    async def test_branch_picker_uses_builtin_footer_instead_of_help_text(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.press("b")
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "BranchPickerScreen")
            self.assertEqual(len(app.screen.query(Footer)), 1)
            self.assertEqual(len(app.screen.query("#branch-picker-help")), 0)

    async def test_languages_panel_is_selectable_and_opens_files_modal(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            self.assertEqual(len(app.screen.query("#language-bar")), 1)
            self.assertEqual(len(app.screen.query("#language-list")), 1)
            scroll = app.screen.query_one("#language-scroll")
            self.assertTrue(scroll.can_focus)
            self.assertGreater(len(app.context.language_stats), 0)

            await pilot.press("tab")
            await pilot.press("tab")
            await pilot.pause()
            self.assertEqual(getattr(app.focused, "id", None), "language-scroll")
            self.assertEqual(app.screen.active_panel, "languages")
            self.assertEqual(app.screen._language_index, 0)

            # arrow keys move the highlight within the panel
            await pilot.press("down")
            await pilot.pause()
            self.assertEqual(app.screen._language_index, 1)

            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "LanguageFilesScreen")

            await pilot.press("escape")
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "MainScreen")

    async def test_branch_picker_restores_current_branch_when_filter_is_cleared(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.press("b")
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "BranchPickerScreen")

            await pilot.press("d", "e", "v")
            await pilot.pause()
            self.assertEqual(app.screen.highlighted_branch, "dev")

            await pilot.press("backspace", "backspace", "backspace")
            await pilot.pause()
            self.assertEqual(
                app.screen.query_one("#branch-picker-filter", Input).value,
                "",
            )
            self.assertEqual(app.screen.highlighted_branch, app.context.current_branch)

    async def test_branch_picker_enter_binding_selects_highlighted_branch(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.press("b")
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "BranchPickerScreen")

            await pilot.press("d", "e", "v")
            await pilot.pause()
            self.assertEqual(app.screen.highlighted_branch, "dev")

            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "MainScreen")
            self.assertEqual(app.screen.selected_branch, "dev")


class FilterTests(unittest.TestCase):
    def test_compact_label_for_change_preserves_filename_and_rename_shape(self) -> None:
        rename_change = CommitFileChange(
            path="src/components/very/long/new_component_name.tsx",
            additions=12,
            deletions=3,
        )
        rename_section = CommitPatchSection(
            path="src/components/very/long/new_component_name.tsx",
            old_path="src/components/very/long/old_component_name.tsx",
            new_path="src/components/very/long/new_component_name.tsx",
            patch="",
            is_binary=False,
            is_rename=True,
        )
        compact = compact_label_for_change(rename_change, rename_section, 32)
        self.assertIn("->", compact)
        self.assertIn(".tsx", compact)
        self.assertLessEqual(len(compact), 32)

    def test_build_language_title_stays_compact_but_keeps_top_language_when_width_allows(self) -> None:
        app = make_app()
        title = build_language_title(
            app.context.language_stats,
            resolve_theme_palette(app.current_theme),
        )
        console = Console(file=io.StringIO(), record=True, width=140)
        console.print(title)
        title_text = console.export_text()
        self.assertIn("Languages", title_text)
        self.assertIn("10", title_text)
        self.assertNotIn("Top", title_text)

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

    def test_render_contribution_graph_month_header_is_stable_across_full_year_views(self) -> None:
        app = make_app()
        palette = resolve_theme_palette(app.current_theme)
        headers: dict[int, str] = {}
        for year in (2023, 2024, 2025, 2026, 2027):
            graph = render_contribution_graph(
                collections.Counter(),
                dt.date(year, 1, 1),
                dt.date(year, 12, 31),
                None,
                palette,
            )
            console = Console(file=io.StringIO(), record=True, width=160)
            console.print(graph)
            headers[year] = console.export_text().splitlines()[0]

        self.assertEqual(len(set(headers.values())), 1)

    def test_resolve_heatmap_colors_follow_theme_palette(self) -> None:
        def color_distance(left: str, right: Color) -> int:
            left_triplet = Color.parse(left).rich_color.triplet
            right_triplet = right.rich_color.triplet
            return (
                (left_triplet.red - right_triplet.red) ** 2
                + (left_triplet.green - right_triplet.green) ** 2
                + (left_triplet.blue - right_triplet.blue) ** 2
            )

        app = make_app()
        base_palette = resolve_theme_palette(app.current_theme)
        alternate_success = (
            base_palette.primary
            if base_palette.primary != base_palette.success
            else base_palette.foreground
        )
        alternate_palette = replace(base_palette, success=alternate_success)

        padding_a, styles_a = resolve_heatmap_colors(base_palette)
        padding_b, styles_b = resolve_heatmap_colors(alternate_palette)

        self.assertEqual(len(styles_a), 5)
        self.assertEqual(len(styles_b), 5)
        self.assertEqual(len(set(styles_a)), 5)
        self.assertEqual(len(set(styles_b)), 5)
        self.assertNotEqual(styles_a[0], padding_a)
        self.assertNotEqual(styles_b[0], padding_b)
        self.assertNotEqual(styles_a[1:], styles_b[1:])
        self.assertLess(
            color_distance(styles_a[-1], base_palette.success),
            color_distance(styles_a[0], base_palette.success),
        )
        self.assertLess(
            color_distance(styles_b[-1], alternate_palette.success),
            color_distance(styles_b[0], alternate_palette.success),
        )

    def test_make_change_stat_style_mutes_zero_and_placeholder_values(self) -> None:
        app = make_app()
        palette = resolve_theme_palette(app.current_theme)

        zero_add_style = make_change_stat_style(palette, 0, positive=True)
        zero_del_style = make_change_stat_style(palette, 0, positive=False)
        none_style = make_change_stat_style(palette, None, positive=True)
        add_style = make_change_stat_style(palette, 3, positive=True)
        del_style = make_change_stat_style(palette, 2, positive=False)

        self.assertEqual(zero_add_style.color, palette.muted.rich_color)
        self.assertEqual(zero_del_style.color, palette.muted.rich_color)
        self.assertEqual(none_style.color, palette.muted.rich_color)
        self.assertFalse(zero_add_style.bold)
        self.assertFalse(zero_del_style.bold)
        self.assertFalse(none_style.bold)
        self.assertEqual(add_style.color, palette.success.rich_color)
        self.assertEqual(del_style.color, palette.error.rich_color)
        self.assertTrue(add_style.bold)
        self.assertTrue(del_style.bold)

    def test_build_commit_identity_line_uses_muted_date_and_regular_author(self) -> None:
        app = make_app()
        palette = resolve_theme_palette(app.current_theme)

        line = build_commit_identity_line(
            palette,
            "2026-01-07",
            "Example User",
            author_max_width=20,
        )
        spans = line.spans

        self.assertEqual(line.plain, "2026-01-07  Example User")
        self.assertEqual(spans[0].style.color, palette.muted.rich_color)
        self.assertEqual(spans[1].style.color, palette.muted.rich_color)
        self.assertEqual(spans[2].style.color, palette.foreground.rich_color)
        self.assertFalse(spans[2].style.bold)

    def test_append_file_change_summary_line_uses_regular_label_and_muted_zero_stats(self) -> None:
        app = make_app()
        palette = resolve_theme_palette(app.current_theme)

        text = Text()
        append_file_change_summary_line(
            text,
            palette,
            "README.md",
            0,
            3,
        )
        spans = text.spans

        self.assertEqual(text.plain, "  - README.md  +0 -3")
        self.assertEqual(spans[0].style.color, palette.muted.rich_color)
        self.assertEqual(spans[1].style.color, palette.foreground.rich_color)
        self.assertFalse(spans[1].style.bold)
        self.assertEqual(spans[3].style.color, palette.muted.rich_color)
        self.assertFalse(spans[3].style.bold)
        self.assertEqual(spans[5].style.color, palette.error.rich_color)
        self.assertTrue(spans[5].style.bold)

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
