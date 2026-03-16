"""Application entrypoint."""

from __future__ import annotations

from textual.app import App

from .models import RepoContext
from .repository import RepoService
from .screens import MainScreen


class GitViewerApp(App[None]):
    """Thin Textual app shell."""

    BINDINGS = [
        ("q", "quit", "Quit"),
    ]

    def __init__(self, context: RepoContext, service: RepoService) -> None:
        super().__init__()
        self.context = context
        self.service = service

    def on_mount(self) -> None:
        self.title = f"{self.context.project_name} - Git Viewer"
        print(f"\033]0;{self.context.project_name} - Git Viewer\007", end="", flush=True)
        self.push_screen(MainScreen(self.context, self.service))
