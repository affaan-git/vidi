"""Core data models for the Textual git viewer."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from textual.color import Color


LanguageStat = dict[str, Any]


@dataclass(frozen=True)
class ThemePalette:
    background: Color
    panel: Color
    surface: Color
    foreground: Color
    muted: Color
    border: Color
    primary: Color
    selected_bg: Color
    selected_fg: Color
    overlay: Color


@dataclass(frozen=True)
class CommitRecord:
    commit_hash: str
    authored_at: dt.datetime
    message: str
    author_name: str
    author_email: str
    changed_files: tuple[str, ...]

    @property
    def authored_date(self) -> dt.date:
        return self.authored_at.date()


@dataclass(frozen=True)
class CommitFileChange:
    path: str
    additions: int | None
    deletions: int | None


@dataclass(frozen=True)
class CommitPatchSection:
    path: str
    old_path: str
    new_path: str
    patch: str
    is_binary: bool
    is_rename: bool


@dataclass(frozen=True)
class CommitDetail:
    commit_hash: str
    parents: tuple[str, ...]
    author_name: str
    author_email: str
    authored_at: dt.datetime
    subject: str
    body: str
    file_changes: tuple[CommitFileChange, ...]
    patch_sections: tuple[CommitPatchSection, ...]
    patch: str


@dataclass(frozen=True)
class BranchData:
    branch: str
    commits: list[CommitRecord]
    years: list[int]


@dataclass(frozen=True)
class RepoContext:
    repo_dir: str
    project_name: str
    repo_commit_count: int
    branches: list[str]
    current_branch: str
    language_stats: list[LanguageStat]
    files_by_language: dict[str, list[str]]
