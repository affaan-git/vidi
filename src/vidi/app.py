"""Application entrypoint."""

from __future__ import annotations

import asyncio

from textual.app import App, SystemCommand
from textual.binding import Binding
from textual.screen import Screen

from . import config
from .models import RepoContext, RepoScanSnapshot
from .repository import RepoService
from .screens import HelpScreen, MainScreen
from .screens.language_settings import LanguageSettingsScreen
from .theme import APP_THEME_NAME, app_themes


class VidiApp(App[None]):
    """Thin app shell."""

    REPO_SCAN_MIN_SECONDS = 10.0
    REPO_SCAN_DEFAULT_SECONDS = 20.0
    REPO_SCAN_MAX_SECONDS = 120.0

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("question_mark", "show_help", "Help"),
    ]

    def __init__(self, context: RepoContext, service: RepoService) -> None:
        super().__init__()
        self.context = context
        self.service = service
        self._repo_scan_snapshot = RepoScanSnapshot(
            branch_heads=context.branch_heads,
            current_branch=context.current_branch,
            repo_commit_count=context.repo_commit_count,
            scanned_file_count=service.scanned_file_count(context),
        )
        self._repo_scan_in_flight = False
        self._repo_scan_task: asyncio.Task[None] | None = None
        self._initial_context_task: asyncio.Task[None] | None = None
        # When True the graph previews a day as you move and selects it on Enter.
        self.graph_browse = config.load_graph_browse()
        # When True the app themes use the terminal's default background.
        self.transparent_background = config.load_transparent_background()

    def on_mount(self) -> None:
        # Register the app themes and restore the saved one, or the default.
        self._theme_source = self.current_theme  # Textual default, derives vidi
        for theme in app_themes(self._theme_source, transparent=self.transparent_background):
            self.register_theme(theme)
        saved = config.load_theme()
        self.theme = saved if saved in self.available_themes else APP_THEME_NAME
        self._apply_transparency()
        self.watch(self, "theme", self._persist_theme, init=False)
        self.title = self.context.project_name
        self.push_screen(MainScreen(self.context, self.service))
        if not self.context.language_stats or not self.context.files_by_language:
            self._initial_context_task = asyncio.create_task(self._warm_initial_context())
        self._schedule_next_repo_scan(self.REPO_SCAN_DEFAULT_SECONDS)

    def _persist_theme(self, theme_name: str) -> None:
        config.save_theme(theme_name)

    def get_system_commands(self, screen: Screen):
        yield from super().get_system_commands(screen)
        yield SystemCommand(
            "Reset theme to default",
            "Switch back to the vidi theme",
            self._command_reset_theme,
        )
        graph_label = (
            "Graph: commit table follows as you move"
            if self.graph_browse
            else "Graph: commit table follows on Enter"
        )
        yield SystemCommand(
            graph_label,
            "Toggle whether moving over the graph moves the commit-table selection "
            "immediately or only when you press Enter",
            self._command_toggle_graph_browse,
        )
        transparent_label = (
            "Opaque background"
            if self.transparent_background
            else "Transparent background"
        )
        yield SystemCommand(
            transparent_label,
            "Toggle whether the app paints its own background or lets the "
            "terminal (and its transparency) show through",
            self._command_toggle_transparent,
        )
        yield SystemCommand(
            "Language settings",
            "Choose how languages are measured and what the scan skips",
            self._command_language_settings,
        )

    def _command_reset_theme(self) -> None:
        self.theme = APP_THEME_NAME

    def _command_language_settings(self) -> None:
        self.push_screen(
            LanguageSettingsScreen(
                self.service.language_mode,
                sorted(self.service.exclude_languages),
                sorted(self.service.extra_ignored_dirs),
            ),
            self._apply_language_settings,
        )

    def _apply_language_settings(self, result: dict | None) -> None:
        if not result:
            return
        self.service.update_language_settings(
            result["language_mode"],
            set(result["exclude_languages"]),
            set(result["ignore_dirs"]),
        )
        config.save_language_settings(
            result["language_mode"],
            result["exclude_languages"],
            result["ignore_dirs"],
        )
        screen = self.screen
        if isinstance(screen, MainScreen):
            screen.reload_language_panel()

    def _command_toggle_graph_browse(self) -> None:
        self.graph_browse = not self.graph_browse
        config.save_graph_browse(self.graph_browse)

    def _command_toggle_transparent(self) -> None:
        self.transparent_background = not self.transparent_background
        config.save_transparent_background(self.transparent_background)
        # Rebuild the app themes for the new background and re-apply the current one.
        for theme in app_themes(self._theme_source, transparent=self.transparent_background):
            self.register_theme(theme)
        self.theme = self.current_theme.name
        self._apply_transparency()
        # Re-registered themes don't reach computed backgrounds without a CSS refresh.
        self.refresh_css()

    def _apply_transparency(self) -> None:
        # ansi_default only stays transparent in native-ANSI mode.
        self.ansi_color = True if self.transparent_background else None

    def on_unmount(self) -> None:
        if self._repo_scan_task is not None:
            self._repo_scan_task.cancel()
            self._repo_scan_task = None
        if self._initial_context_task is not None:
            self._initial_context_task.cancel()
            self._initial_context_task = None

    def _schedule_next_repo_scan(self, delay_seconds: float) -> None:
        self.set_timer(delay_seconds, self._trigger_repo_scan)

    def _next_repo_scan_delay(self, duration_seconds: float) -> float:
        return min(
            self.REPO_SCAN_MAX_SECONDS,
            max(self.REPO_SCAN_MIN_SECONDS, duration_seconds * 30.0),
        )

    def _trigger_repo_scan(self) -> None:
        if self._repo_scan_in_flight:
            self._schedule_next_repo_scan(self.REPO_SCAN_MIN_SECONDS)
            return
        self._repo_scan_task = asyncio.create_task(self._scan_repo_state())

    def _apply_refreshed_context(
        self,
        previous_context: RepoContext,
        new_context: RepoContext,
        previous_snapshot: RepoScanSnapshot,
        new_snapshot: RepoScanSnapshot,
    ) -> None:
        self.context = new_context
        self._repo_scan_snapshot = new_snapshot
        for screen in list(self.screen_stack):
            handler = getattr(screen, "handle_repo_context_refresh", None)
            if callable(handler):
                handler(previous_context, new_context, previous_snapshot, new_snapshot)

    async def _warm_initial_context(self) -> None:
        try:
            previous_context = self.context
            previous_snapshot = self._repo_scan_snapshot
            new_context = await asyncio.to_thread(self.service.refresh_context)
            new_snapshot = RepoScanSnapshot(
                branch_heads=new_context.branch_heads,
                current_branch=new_context.current_branch,
                repo_commit_count=new_context.repo_commit_count,
                scanned_file_count=self.service.scanned_file_count(new_context),
            )
            self._apply_refreshed_context(
                previous_context,
                new_context,
                previous_snapshot,
                new_snapshot,
            )
        finally:
            self._initial_context_task = None

    async def _scan_repo_state(self) -> None:
        self._repo_scan_in_flight = True
        duration = self.REPO_SCAN_DEFAULT_SECONDS
        try:
            snapshot, duration = await asyncio.to_thread(self.service.scan_repo_state)
            previous_snapshot = self._repo_scan_snapshot
            self._repo_scan_snapshot = snapshot
            if snapshot != previous_snapshot:
                self.service.invalidate_cached_git_data()
                previous_context = self.context
                new_context = await asyncio.to_thread(self.service.refresh_context)
                self._apply_refreshed_context(
                    previous_context,
                    new_context,
                    previous_snapshot,
                    snapshot,
                )
        finally:
            self._repo_scan_in_flight = False
            self._repo_scan_task = None
            self._schedule_next_repo_scan(self._next_repo_scan_delay(duration))

    def action_show_help(self) -> None:
        if isinstance(self.screen, HelpScreen):
            return
        self.push_screen(HelpScreen())
