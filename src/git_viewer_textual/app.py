"""Application entrypoint."""

from __future__ import annotations

import asyncio

from textual.app import App

from .models import RepoContext, RepoScanSnapshot
from .repository import RepoService
from .screens import MainScreen


class GitViewerApp(App[None]):
    """Thin Textual app shell."""

    REPO_SCAN_MIN_SECONDS = 10.0
    REPO_SCAN_DEFAULT_SECONDS = 20.0
    REPO_SCAN_MAX_SECONDS = 120.0

    BINDINGS = [
        ("q", "quit", "Quit"),
    ]

    def __init__(self, context: RepoContext, service: RepoService) -> None:
        super().__init__()
        self.context = context
        self.service = service
        self._repo_scan_snapshot = RepoScanSnapshot(
            branch_heads=context.branch_heads,
            current_branch=context.current_branch,
            repo_commit_count=context.repo_commit_count,
            scanned_file_count=sum(len(files) for files in context.files_by_language.values()),
        )
        self._repo_scan_in_flight = False
        self._repo_scan_task: asyncio.Task[None] | None = None

    def on_mount(self) -> None:
        self.title = f"{self.context.project_name} - Git Viewer"
        print(f"\033]0;{self.context.project_name} - Git Viewer\007", end="", flush=True)
        self.push_screen(MainScreen(self.context, self.service))
        self._schedule_next_repo_scan(self.REPO_SCAN_DEFAULT_SECONDS)

    def on_unmount(self) -> None:
        if self._repo_scan_task is not None:
            self._repo_scan_task.cancel()
            self._repo_scan_task = None

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
                self.context = new_context
                for screen in list(self.screen_stack):
                    handler = getattr(screen, "handle_repo_context_refresh", None)
                    if callable(handler):
                        handler(previous_context, new_context, previous_snapshot, snapshot)
        finally:
            self._repo_scan_in_flight = False
            self._repo_scan_task = None
            self._schedule_next_repo_scan(self._next_repo_scan_delay(duration))
