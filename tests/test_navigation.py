from __future__ import annotations

import collections
import contextlib
import datetime as dt
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import replace

from rich.console import Console
from rich.text import Text
from textual.color import Color
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header, OptionList
from textual.widgets import Input

from vidi.app import VidiApp
from vidi.cli import build_parser, package_version, resolve_repo_dir
from vidi.docs import MAN_DIR, PAGES, README, build_manpage, update_readme
from vidi.screens.help import SHORTCUTS
from vidi.data import (
    compact_label_for_change,
    compact_path_label,
    filter_commits,
    resolve_graph_window,
)
from vidi.formatting import (
    append_file_change_summary_line,
    build_commit_identity_line,
    build_repo_stats_sub_title,
)
from vidi.models import (
    CommitFileChange,
    CommitPatchSection,
    CommitRecord,
    RepoScanSnapshot,
)
from vidi.render import (
    build_language_bar,
    build_language_summary,
    build_language_title,
    render_contribution_graph,
    resolve_heatmap_colors,
)
from vidi.repository import (
    RepoService,
    build_initial_repo_context,
    build_repo_context,
    get_branch_heads,
    parse_gitignore,
)
from vidi.theme import make_change_stat_style, resolve_theme_palette

from sample_repo import build_sample_repo, sample_repo


def make_app() -> VidiApp:
    context, service = build_repo_context(
        repo_dir=sample_repo(),
        language_mode="bytes",
        exclude_languages=set(),
        extra_ignored_dirs=set(),
    )
    return VidiApp(context, service)


def make_lightweight_app() -> VidiApp:
    context, service = build_initial_repo_context(
        repo_dir=sample_repo(),
        language_mode="bytes",
        exclude_languages=set(),
        extra_ignored_dirs=set(),
    )
    return VidiApp(context, service)


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
                build_repo_stats_sub_title(
                    app.context, expected_count, app.screen.selected_branch, 0, 0
                ),
            )
            # the branch leads the header
            self.assertTrue(app.screen.sub_title.startswith(app.screen.selected_branch))
            # counts are singular when they should be (1 file, not 1 files)
            one_file = build_repo_stats_sub_title(app.context, 1, "b", 0, 0)
            self.assertIn("1 file", one_file)
            self.assertNotIn("1 files", one_file)

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
        # The deferred context starts with no language data, requiring on_mount to
        # start the warm-up scan that fills it in.
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
            branch = app.screen.source_branch or app.context.current_branch
            self.assertEqual(
                app.screen.sub_title,
                build_repo_stats_sub_title(app.context, expected_count, branch, 0, 0),
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
            for name in ("vidi", "one-night", "pro-black"):
                self.assertIn(name, app.available_themes)
            self.assertIn(app.current_theme.name, app.available_themes)

    def test_config_theme_roundtrip(self) -> None:
        import pathlib
        import tempfile

        from vidi import config

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
                {"Back", "Pane", "Move", "Page", "Jump", "Commit", "Search"},
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

    def test_commit_language_stats_are_a_per_commit_snapshot(self) -> None:
        context, service = build_repo_context(
            repo_dir=sample_repo(),
            language_mode="bytes",
            exclude_languages=set(),
            extra_ignored_dirs=set(),
        )
        head = service.get_branch_data(context.current_branch).commits[0].commit_hash
        stats, files_by_language = service.get_commit_language_stats(head)
        self.assertGreater(len(stats), 0)
        for stat in stats:
            self.assertIn("count", stat)
            self.assertIn("percentage", stat)
            self.assertEqual(stat["count"], len(files_by_language[stat["language"]]))

    async def test_languages_panel_is_per_commit_and_opens_file_viewer(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            self.assertEqual(len(app.screen.query("#language-bar")), 1)
            self.assertTrue(app.screen.query_one("#language-scroll").can_focus)
            # the panel reflects the selected commit's tree, with per-language counts
            self.assertGreater(len(app.screen._language_stats), 0)
            self.assertTrue(all("count" in s for s in app.screen._language_stats))

            await pilot.press("tab")
            await pilot.press("tab")
            await pilot.pause()
            self.assertEqual(getattr(app.focused, "id", None), "language-scroll")
            self.assertEqual(app.screen.active_panel, "languages")

            await pilot.press("down")
            await pilot.pause()
            self.assertEqual(app.screen._language_index, 1)

            # Enter opens the files modal for the highlighted language
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "CommitFilesScreen")
            self.assertGreater(
                app.screen.query_one("#commit-files-list", OptionList).option_count, 0
            )

            # Enter on a file opens the file viewer with its content
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "FileViewScreen")
            self.assertEqual(len(app.screen.query("#file-view-content")), 1)

            await pilot.press("escape")
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "CommitFilesScreen")
            await pilot.press("escape")
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "MainScreen")

    async def test_click_selects_a_language_in_the_legend(self) -> None:
        from textual.geometry import Offset

        app = make_app()
        async with app.run_test(size=(160, 44)) as pilot:
            rows = app.screen._language_rows()
            self.assertTrue(rows)
            await pilot.click("#language-scroll", offset=Offset(2, 0))
            await pilot.pause()
            self.assertEqual(app.screen.active_panel, "languages")
            self.assertEqual(app.screen._language_index, rows[0][0])

    async def test_click_selects_a_graph_day(self) -> None:
        import datetime as dt

        from textual.geometry import Offset

        from vidi.data import resolve_graph_window

        app = make_app()
        async with app.run_test(size=(160, 44)) as pilot:
            scr = app.screen
            start, _ = resolve_graph_window(scr.selected_range, scr.selected_year)
            first_sunday = start - dt.timedelta(days=(start.weekday() + 1) % 7)
            offset = (scr.selected_day - first_sunday).days
            column, row = offset // 7 + 4, offset % 7 + 1
            await pilot.click("#graph-content", offset=Offset(column, row))
            await pilot.pause()
            self.assertEqual(getattr(app.focused, "id", None), "graph-content")
            self.assertEqual(app.screen.active_panel, "graph")

    async def test_file_view_steps_between_files(self) -> None:
        from vidi.screens.file_view import FileViewScreen

        context, service = build_repo_context(
            repo_dir=sample_repo(),
            language_mode="bytes",
            exclude_languages=set(),
            extra_ignored_dirs=set(),
        )
        head = service.get_branch_data(context.current_branch).commits[0].commit_hash
        _, files_by_language = service.get_commit_language_stats(head)
        files = sorted(p for paths in files_by_language.values() for p in paths)[:2]
        self.assertGreaterEqual(len(files), 2)

        app = VidiApp(context, service)
        async with app.run_test() as pilot:
            app.push_screen(FileViewScreen(service, head, files, 0))
            await pilot.pause()
            self.assertEqual(app.screen._index, 0)
            await pilot.press("]")
            await pilot.pause()
            self.assertEqual(app.screen._index, 1)
            await pilot.press("[")
            await pilot.pause()
            self.assertEqual(app.screen._index, 0)

    async def test_commit_files_browser_steps_commits(self) -> None:
        from vidi.screens.commit_files import CommitFilesScreen

        context, service = build_repo_context(
            repo_dir=sample_repo(),
            language_mode="bytes",
            exclude_languages=set(),
            extra_ignored_dirs=set(),
        )
        hashes = [c.commit_hash for c in service.get_branch_data(context.current_branch).commits]
        self.assertGreaterEqual(len(hashes), 2)

        app = VidiApp(context, service)
        async with app.run_test(size=(120, 40)) as pilot:
            # start on an older commit, with commits on both sides
            app.push_screen(CommitFilesScreen(service, hashes, 1))
            await pilot.pause()
            self.assertEqual(app.screen._commit_hash, hashes[1])

            await pilot.press("]")  # newer
            await pilot.pause()
            self.assertEqual(app.screen._commit_hash, hashes[0])

            await pilot.press("[")  # older
            await pilot.pause()
            self.assertEqual(app.screen._commit_hash, hashes[1])
            # the file list follows the commit
            self.assertEqual(app.screen._files, service.get_commit_files(hashes[1]))

    async def test_commit_files_browser_marks_changed_and_filters(self) -> None:
        import re

        from vidi.screens.commit_files import CommitFilesScreen

        context, service = build_repo_context(
            repo_dir=sample_repo(),
            language_mode="bytes",
            exclude_languages=set(),
            extra_ignored_dirs=set(),
        )
        hashes = [c.commit_hash for c in service.get_branch_data(context.current_branch).commits]
        head = hashes[0]
        changed = {fc.path for fc in service.get_commit_detail(head).file_changes}

        app = VidiApp(context, service)
        async with app.run_test(size=(120, 40)) as pilot:
            app.push_screen(CommitFilesScreen(service, hashes, 0))
            await pilot.pause()
            await pilot.pause()
            screen = app.screen
            # files changed in this commit are tracked (and dotted in the list)
            self.assertEqual(screen._changed, changed)

            # / opens the filter and narrows the list live
            await pilot.press("slash")
            await pilot.pause()
            self.assertTrue(screen.query_one("#commit-files-filter", Input).display)
            fragment = re.match(r"[A-Za-z]+", screen._all_files[0].split("/")[-1])
            fragment = (fragment.group(0) if fragment else "a")[:4]
            for char in fragment:
                await pilot.press(char)
            await pilot.pause()
            self.assertTrue(all(fragment.lower() in path.lower() for path in screen._files))
            self.assertLessEqual(len(screen._files), len(screen._all_files))

            # Enter keeps the filter and moves into the list
            await pilot.press("enter")
            await pilot.pause()
            self.assertTrue(screen._options().has_focus)

            # Esc clears the filter and restores the full list, screen stays open
            await pilot.press("escape")
            await pilot.pause()
            self.assertFalse(screen.query_one("#commit-files-filter", Input).display)
            self.assertEqual(len(screen._files), len(screen._all_files))
            self.assertIsInstance(app.screen, CommitFilesScreen)

    async def test_commit_table_filter(self) -> None:
        app = make_app()
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.pause()
            screen = app.screen
            table = screen.query_one("#commits-table", DataTable)
            base_rows = table.row_count
            self.assertGreaterEqual(base_rows, 1)

            # _visible_commits narrows on hash, and on author
            commits = screen.service.get_branch_data(screen.selected_branch).commits
            screen._commit_query = commits[0].commit_hash[:6]
            self.assertEqual(
                [c.commit_hash for c in screen._visible_commits(commits)],
                [commits[0].commit_hash],
            )
            screen._commit_query = commits[0].author_name
            self.assertTrue(
                all(
                    commits[0].author_name.lower() in c.author_name.lower()
                    for c in screen._visible_commits(commits)
                )
            )
            # and on the authored date (ISO, e.g. a year-month prefix)
            screen._commit_query = commits[0].authored_date.isoformat()[:7]
            self.assertIn(
                commits[0].commit_hash,
                [c.commit_hash for c in screen._visible_commits(commits)],
            )
            screen._commit_query = ""

            # / opens the filter, typing narrows, and j/k type instead of navigating
            await pilot.press("slash")
            await pilot.pause()
            filter_input = screen.query_one("#commits-filter", Input)
            self.assertTrue(filter_input.display and filter_input.has_focus)
            for char in "zzz":
                await pilot.press(char)
            await pilot.pause()
            self.assertEqual(filter_input.value, "zzz")
            self.assertEqual(table.row_count, 0)
            for char in "jk":
                await pilot.press(char)
            await pilot.pause()
            self.assertEqual(filter_input.value, "zzzjk")

            # Esc clears the filter and restores the table with focus
            await pilot.press("escape")
            await pilot.pause()
            self.assertFalse(filter_input.display)
            self.assertEqual(table.row_count, base_rows)
            self.assertTrue(table.has_focus)

    async def test_browse_files_key_opens_all_files_from_main_and_detail(self) -> None:
        from vidi.screens.commit_files import CommitFilesScreen

        app = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            # from the main screen (any panel), f browses the whole tree
            await pilot.press("f")
            await pilot.pause()
            await pilot.pause()
            self.assertIsInstance(app.screen, CommitFilesScreen)
            self.assertIsNone(app.screen._language)
            self.assertGreater(
                app.screen.query_one("#commit-files-list", OptionList).option_count, 0
            )
            await pilot.press("escape")
            await pilot.pause()

            # and from inside the commit detail screen
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "CommitDetailScreen")
            await pilot.press("f")
            await pilot.pause()
            await pilot.pause()
            self.assertIsInstance(app.screen, CommitFilesScreen)

    async def test_file_view_in_file_search(self) -> None:
        from vidi.screens.file_view import FileViewScreen

        context, service = build_repo_context(
            repo_dir=sample_repo(),
            language_mode="bytes",
            exclude_languages=set(),
            extra_ignored_dirs=set(),
        )
        head = service.get_branch_data(context.current_branch).commits[0].commit_hash
        best, best_lines = None, []
        for path in service.get_commit_files(head):
            content = service.get_file_at_commit(head, path)
            if content is None:
                continue
            lines = content.splitlines() or [""]
            if len(lines) > len(best_lines):
                best, best_lines = path, lines
        self.assertIsNotNone(best)
        query = next(
            (ch for ch in "etaoinshr" if sum(ch in ln.lower() for ln in best_lines) >= 2),
            None,
        )
        self.assertIsNotNone(query)
        expected = [i for i, ln in enumerate(best_lines) if query in ln.lower()]
        self.assertGreaterEqual(len(expected), 2)

        app = VidiApp(context, service)
        async with app.run_test(size=(100, 30)) as pilot:
            app.push_screen(FileViewScreen(service, head, [best], 0))
            await pilot.pause()
            await pilot.pause()
            screen = app.screen

            await pilot.press("slash")
            await pilot.pause()
            self.assertTrue(screen.query_one("#file-view-search", Input).display)
            await pilot.press(query)
            await pilot.pause()
            self.assertEqual(screen._match_lines, expected)
            self.assertEqual(screen._current_match, 0)

            # Enter steps to the next match while the box is focused
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(screen._current_match, 1)

            # Esc closes the box, keeps the matches, focuses the content
            await pilot.press("escape")
            await pilot.pause()
            self.assertFalse(screen.query_one("#file-view-search", Input).display)
            self.assertTrue(screen._scroll().has_focus)
            self.assertEqual(screen._match_lines, expected)

            # n / N step matches on the content
            await pilot.press("N")
            await pilot.pause()
            self.assertEqual(screen._current_match, 0)
            await pilot.press("n")
            await pilot.pause()
            self.assertEqual(screen._current_match, 1)

            # Esc again closes the screen
            await pilot.press("escape")
            await pilot.pause()
            self.assertNotEqual(type(app.screen).__name__, "FileViewScreen")

    async def test_commit_detail_patch_search(self) -> None:
        app = make_app()
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "CommitDetailScreen")
            screen = app.screen

            # full patch gives a larger body to search
            await pilot.press("p")
            await pilot.pause()
            await pilot.pause()
            self.assertTrue(screen.show_full_patch)
            lines = screen._patch_base["full"].plain.split("\n")
            token = next(
                (c for c in "etaoinsr" if sum(c in ln.lower() for ln in lines) >= 2), None
            )
            self.assertIsNotNone(token)
            expected = [i for i, ln in enumerate(lines) if token in ln.lower()]
            self.assertGreaterEqual(len(expected), 2)

            await pilot.press("slash")
            await pilot.pause()
            self.assertTrue(screen.query_one("#commit-detail-search", Input).display)
            await pilot.press(token)
            await pilot.pause()
            self.assertEqual(screen._match_lines, expected)
            self.assertEqual(screen._current_match, 0)

            # Enter steps to the next match while the box is focused
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(screen._current_match, 1)

            # Esc closes the box, keeps the matches, focuses the patch
            await pilot.press("escape")
            await pilot.pause()
            self.assertFalse(screen.query_one("#commit-detail-search", Input).display)
            self.assertTrue(screen._active_patch_scroll().has_focus)
            self.assertEqual(screen._match_lines, expected)

            # n / N step matches in the patch
            await pilot.press("N")
            await pilot.pause()
            self.assertEqual(screen._current_match, 0)
            await pilot.press("n")
            await pilot.pause()
            self.assertEqual(screen._current_match, 1)

            # Esc again closes the screen
            await pilot.press("escape")
            await pilot.pause()
            self.assertEqual(type(app.screen).__name__, "MainScreen")

    async def test_branch_picker_shows_ahead_behind(self) -> None:
        from vidi.screens.branch_picker import BranchPickerScreen

        app = make_app()
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            divergence = {"main": (0, 0), "feature": (2, 1), "hotfix": (5, 0)}
            app.push_screen(
                BranchPickerScreen(
                    ["main", "feature", "hotfix"], "main", divergence, "main"
                )
            )
            await pilot.pause()
            await pilot.pause()
            options = app.screen.query_one("#branch-picker-options", OptionList)
            rendered = {
                str(options.get_option_at_index(i).prompt.plain)
                for i in range(options.option_count)
            }
            joined = " | ".join(rendered)
            self.assertIn("↑2", joined)
            self.assertIn("↓1", joined)
            self.assertIn("↑5", joined)
            # the base branch itself carries no ahead/behind marks
            self.assertTrue(any(r.startswith("main") and "↑" not in r for r in rendered))

    async def test_commit_detail_files_filter(self) -> None:
        import re

        from vidi.screens.commit_detail import CommitDetailScreen

        context, service = build_repo_context(
            repo_dir=sample_repo(),
            language_mode="bytes",
            exclude_languages=set(),
            extra_ignored_dirs=set(),
        )
        hashes = [c.commit_hash for c in service.get_branch_data(context.current_branch).commits]
        target = next(
            (h for h in hashes if len(service.get_commit_detail(h).file_changes) >= 2),
            hashes[0],
        )
        paths = [c.path for c in service.get_commit_detail(target).file_changes]
        self.assertGreaterEqual(len(paths), 2)

        app = VidiApp(context, service)
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.pause()
            app.push_screen(
                CommitDetailScreen(
                    service, target, source_branch=context.current_branch, commit_hashes=hashes
                )
            )
            await pilot.pause()
            await pilot.pause()
            screen = app.screen
            table = screen._current_files_table()
            table.focus()
            await pilot.pause()
            self.assertEqual(screen.active_pane, "files")
            self.assertEqual(table.row_count, len(paths))

            # / over the files pane (diff mode) opens the files filter
            await pilot.press("slash")
            await pilot.pause()
            self.assertTrue(screen.query_one("#commit-detail-files-filter", Input).display)
            fragment = re.search(r"[A-Za-z]{3,}", paths[0].split("/")[-1])
            fragment = (fragment.group(0) if fragment else "aaa")[:4]
            for char in fragment:
                await pilot.press(char)
            await pilot.pause()
            expected = sum(1 for p in paths if fragment.lower() in p.lower())
            self.assertGreaterEqual(expected, 1)
            self.assertEqual(table.row_count, expected)

            # Esc clears the filter and restores the full list
            await pilot.press("escape")
            await pilot.pause()
            self.assertFalse(screen.query_one("#commit-detail-files-filter", Input).display)
            self.assertEqual(table.row_count, len(paths))

    async def test_language_settings_apply_updates_scan_and_persists(self) -> None:
        from vidi import config
        from vidi.screens.language_settings import LanguageSettingsScreen
        from textual.widgets import Select

        context, service = build_repo_context(
            repo_dir=sample_repo(),
            language_mode="bytes",
            exclude_languages=set(),
            extra_ignored_dirs=set(),
        )
        head = service.get_branch_data(context.current_branch).commits[0].commit_hash
        before = [s["language"] for s in service.get_commit_language_stats(head)[0]]
        self.assertGreater(len(before), 0)
        victim = before[0]

        app = VidiApp(context, service)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._command_language_settings()
            await pilot.pause()
            self.assertIsInstance(app.screen, LanguageSettingsScreen)
            app.screen.query_one("#language-mode", Select).value = "files"
            app.screen.query_one("#language-exclude", Input).value = victim
            app.screen.action_apply()
            await pilot.pause()

            self.assertEqual(service.language_mode, "files")
            self.assertIn(victim, service.exclude_languages)
            after = [s["language"] for s in service.get_commit_language_stats(head)[0]]
            self.assertNotIn(victim, after)

        saved = config.load_language_settings()
        self.assertEqual(saved["language_mode"], "files")
        self.assertIn(victim, saved["exclude_languages"])

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

    def test_compact_path_label_narrow_widths(self) -> None:
        # a root file never gets a misleading ellipsis + "/" parent prefix
        for width in range(5, 12):
            root = compact_path_label("README.md", width)
            self.assertFalse(root.startswith("…/"), (width, root))
            self.assertLessEqual(len(root), width)
            self.assertEqual(root.count("…"), 0 if root == "README.md" else 1, (width, root))

        # a nested file keeps its start and extension with a single ellipsis
        nested = compact_path_label("src/a/commit_files.py", 12)
        self.assertLessEqual(len(nested), 12)
        self.assertTrue(nested.endswith(".py"), nested)
        self.assertEqual(nested.count("…"), 1, nested)
        self.assertTrue(nested.startswith("commit"), nested)

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

    def test_language_bar_keeps_tiny_languages_visible_and_highlightable(self) -> None:
        app = make_app()
        palette = resolve_theme_palette(app.current_theme)
        stats = [
            {"language": "Python", "count": 23, "percentage": 97.9, "color": "#3572A5"},
            {"language": "Markdown", "count": 1, "percentage": 1.9, "color": "#083fa1"},
            {"language": "TOML", "count": 1, "percentage": 0.15, "color": "#6d5cae"},
            {"language": "Other", "count": 1, "percentage": 0.05, "color": "#8b5cf6"},
        ]
        bar = build_language_bar(stats, palette, width=40)
        # Every listed language gets at least one visible cell.
        self.assertEqual(len(bar.spans), len(stats))
        self.assertEqual(len(bar.plain), 40)
        # Selecting a tiny language highlights its segment (bold + brightened).
        highlighted = build_language_bar(stats, palette, width=40, highlighted_index=3)
        self.assertNotIn("bold", str(bar.spans[3].style))
        self.assertIn("bold", str(highlighted.spans[3].style))

    def test_language_summary_shows_less_than_one_percent_for_tiny_shares(self) -> None:
        app = make_app()
        palette = resolve_theme_palette(app.current_theme)
        stats = [
            {"language": "Python", "count": 23, "percentage": 97.9, "color": "#3572A5"},
            {"language": "TOML", "count": 1, "percentage": 0.15, "color": "#6d5cae"},
        ]
        summary = build_language_summary(stats, palette, width=80)
        console = Console(file=io.StringIO(), record=True, width=80)
        console.print(summary)
        text = console.export_text()
        self.assertIn("TOML 1 (<1%)", text)
        self.assertIn("Python 23 (98%)", text)
        self.assertNotIn("(0%)", text)

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

    def test_contribution_graph_shows_selected_day_readout(self) -> None:
        app = make_app()
        graph = render_contribution_graph(
            collections.Counter({dt.date(2026, 3, 15): 3}),
            dt.date(2026, 3, 1),
            dt.date(2026, 3, 31),
            dt.date(2026, 3, 15),
            resolve_theme_palette(app.current_theme),
        )
        console = Console(file=io.StringIO(), record=True, width=160)
        console.print(graph)
        text = console.export_text()
        self.assertIn("Sun, Mar 15 2026", text)
        self.assertIn("3 commits", text)

    def test_contribution_graph_readout_handles_empty_day_and_no_selection(self) -> None:
        app = make_app()
        palette = resolve_theme_palette(app.current_theme)
        # A selected day with no commits reads "no commits".
        graph = render_contribution_graph(
            collections.Counter({dt.date(2026, 3, 16): 1}),
            dt.date(2026, 3, 1),
            dt.date(2026, 3, 31),
            dt.date(2026, 3, 12),
            palette,
        )
        console = Console(file=io.StringIO(), record=True, width=160)
        console.print(graph)
        self.assertIn("no commits", console.export_text())
        # With no selection the legend has no date or count.
        graph_none = render_contribution_graph(
            collections.Counter(),
            dt.date(2026, 3, 1),
            dt.date(2026, 3, 31),
            None,
            palette,
        )
        console_none = Console(file=io.StringIO(), record=True, width=160)
        console_none.print(graph_none)
        self.assertNotIn("commit", console_none.export_text())

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


class ResolveRepoDirTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = os.path.realpath(tempfile.mkdtemp(prefix="vidi-cli-"))
        cls.repo = os.path.join(cls.tmp, "repo")
        os.makedirs(cls.repo)
        build_sample_repo(cls.repo)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_defaults_to_the_current_folder(self) -> None:
        cwd = os.getcwd()
        os.chdir(self.repo)
        try:
            self.assertEqual(resolve_repo_dir("."), self.repo)
        finally:
            os.chdir(cwd)

    def test_subfolder_resolves_to_the_repo_top(self) -> None:
        self.assertEqual(resolve_repo_dir(os.path.join(self.repo, "src")), self.repo)

    def test_worktree_resolves_and_loads(self) -> None:
        worktree = os.path.join(self.tmp, "wt")
        subprocess.run(
            ["git", "-C", self.repo, "worktree", "add", "--detach", worktree],
            check=True,
            capture_output=True,
        )
        self.assertTrue(os.path.isfile(os.path.join(worktree, ".git")))
        top = resolve_repo_dir(os.path.join(worktree, "web"))
        self.assertEqual(top, worktree)
        context, _ = build_repo_context(
            repo_dir=top,
            language_mode="bytes",
            exclude_languages=set(),
            extra_ignored_dirs=set(),
        )
        self.assertGreater(context.repo_commit_count, 0)
        self.assertTrue(context.language_stats)
        scanned = [path for paths in context.files_by_language.values() for path in paths]
        self.assertNotIn(".git", scanned)

    def test_outside_a_repo_is_an_error(self) -> None:
        outside = os.path.join(self.tmp, "plain")
        os.makedirs(outside, exist_ok=True)
        with self.assertRaisesRegex(ValueError, "not inside a git work tree"):
            resolve_repo_dir(outside)
        with self.assertRaisesRegex(ValueError, "does not exist"):
            resolve_repo_dir(os.path.join(self.tmp, "missing"))


class VersionTests(unittest.TestCase):
    def test_version_flag_prints_the_package_version(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as exit_:
            build_parser("git-view").parse_args(["--version"])
        self.assertEqual(exit_.exception.code, 0)
        self.assertEqual(out.getvalue().strip(), f"vidi {package_version()}")
        self.assertNotEqual(package_version(), "unknown")


class HostileRepoTests(unittest.IsolatedAsyncioTestCase):
    def _repo(self, tmp: str) -> str:
        repo = os.path.join(tmp, "repo")
        os.makedirs(repo)
        self._git(repo, "init", "-q", "-b", "main")
        return repo

    def _git(self, repo: str, *args: str) -> None:
        subprocess.run(
            ["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
            check=True,
            capture_output=True,
        )

    def test_branch_named_like_an_option_is_read_as_a_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(tmp)
            self._git(repo, "commit", "-q", "--allow-empty", "-m", "first")
            target = os.path.join(tmp, "written")
            self._git(repo, "update-ref", f"refs/heads/--output={target}", "HEAD")
            service = RepoService(repo)
            branches, _, _ = get_branch_heads(repo)
            self.assertEqual(len(branches), 2)
            for branch in branches:
                self.assertEqual(len(service.get_branch_data(branch).commits), 1)
                service.get_branch_divergence(branch, "main")
            self.assertFalse(os.path.exists(target))

    def test_repo_config_commands_do_not_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(tmp)
            marker = os.path.join(tmp, "ran")
            with open(os.path.join(repo, "a.py"), "w") as handle:
                handle.write("x = 1\n")
            with open(os.path.join(repo, ".gitattributes"), "w") as handle:
                handle.write("*.py diff=hostile\n")
            self._git(repo, "add", "-A")
            self._git(repo, "commit", "-q", "-m", "first")
            self._git(repo, "config", "diff.hostile.textconv", f"touch {marker}; cat")
            self._git(repo, "config", "core.fsmonitor", f"touch {marker}; true")
            context, service = build_repo_context(
                repo_dir=repo, language_mode="bytes", exclude_languages=set(), extra_ignored_dirs=set()
            )
            head = service.get_branch_data("main").commits[0].commit_hash
            service.get_commit_detail(head)
            service.get_file_at_commit(head, "a.py")
            service.scan_repo_state()
            self.assertFalse(os.path.exists(marker))

    def test_escape_sequences_in_repo_text_are_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(tmp)
            with open(os.path.join(repo, "a.txt"), "w") as handle:
                handle.write("before\x1b]0;title\x07after\n")
            self._git(repo, "add", "-A")
            subprocess.run(
                ["git", "-C", repo, "-c", "user.name=t\x1b[31m", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                 "commit", "-q", "-m", "msg \x1b[2J end"],
                check=True,
                capture_output=True,
            )
            service = RepoService(repo)
            commit = service.get_branch_data("main").commits[0]
            content = service.get_file_at_commit(commit.commit_hash, "a.txt")
            for text in (commit.message, commit.author_name, content):
                self.assertNotIn("\x1b", text)
                self.assertIn("\ufffd", text)

    def test_gitignore_is_only_read_when_a_small_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(tmp)
            gitignore = os.path.join(repo, ".gitignore")
            outside = os.path.join(tmp, "outside")
            with open(outside, "w") as handle:
                handle.write("from-outside\n")
            os.symlink(outside, gitignore)
            self.assertEqual(parse_gitignore(repo), set())
            os.remove(gitignore)
            with open(gitignore, "w") as handle:
                handle.write("too-big\n" + "#" * 1_000_000 + "\n")
            self.assertEqual(parse_gitignore(repo), set())
            with open(gitignore, "w") as handle:
                handle.write("node_modules\n")
            self.assertEqual(parse_gitignore(repo), {"node_modules"})

    async def test_markup_in_messages_and_paths_is_shown_as_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(tmp)
            self._git(repo, "commit", "-q", "--allow-empty", "-m", "fix [/] handling")
            self._git(repo, "commit", "-q", "--allow-empty", "-m", "[ci skip] bump version")
            os.makedirs(os.path.join(repo, "[x]"))
            with open(os.path.join(repo, "[x]", "a.py"), "w") as handle:
                handle.write("x = 1\n")
            self._git(repo, "add", "-A")
            self._git(repo, "commit", "-q", "-m", "add [slug] route")
            context, service = build_repo_context(
                repo_dir=repo, language_mode="bytes", exclude_languages=set(), extra_ignored_dirs=set()
            )
            app = VidiApp(context, service)
            async with app.run_test(size=(160, 45)) as pilot:
                await pilot.pause()
                table = app.screen.query_one("#commits-table", DataTable)
                shown = [table.get_row_at(row)[1].plain for row in range(table.row_count)]
                self.assertEqual(shown, ["add [slug] route", "[ci skip] bump version", "fix [/] handling"])
                await pilot.press("enter")
                await pilot.pause()
                files = app.screen.query_one("#commit-detail-files-diff", DataTable)
                self.assertEqual(files.get_row_at(0)[0].plain, "[x]/a.py")


class ManPageTests(unittest.TestCase):
    def test_shipped_pages_match_the_sources(self) -> None:
        for prog in PAGES:
            shipped = (MAN_DIR / f"{prog}.1").read_text()
            self.assertEqual(
                shipped,
                build_manpage(prog),
                f"man/{prog}.1 is stale. Run python -m vidi.docs",
            )

    def test_readme_key_tables_match_the_sources(self) -> None:
        text = README.read_text()
        self.assertEqual(
            text, update_readme(text), "README key tables are stale. Run python -m vidi.docs"
        )
        for section, keys in SHORTCUTS:
            self.assertIn(f"### {section}", text)
            for _key, description in keys:
                self.assertIn(f"| {description} |", text)

    def test_page_covers_options_and_every_key(self) -> None:
        page = build_manpage("git-view")
        self.assertIn(".B git\\-view [\\-h] [\\-\\-version] [repo_dir]", page)
        for section, keys in SHORTCUTS:
            self.assertIn(f".SS {section}", page)
            for _key, description in keys:
                self.assertIn(description.replace("-", "\\-"), page)
