"""Shared text-formatting helpers."""

from __future__ import annotations

from rich.text import Text

from .models import RepoContext, ThemePalette
from .theme import make_change_stat_style, make_style
from .utils import truncate_middle


def build_commit_identity_line(
    palette: ThemePalette,
    authored_label: str,
    author_name: str,
    *,
    author_max_width: int | None = None,
) -> Text:
    text = Text()
    text.append(authored_label, style=make_style(palette.muted))
    text.append("  ", style=make_style(palette.muted))
    text.append(
        truncate_middle(author_name, author_max_width)
        if author_max_width is not None
        else author_name,
        style=make_style(palette.foreground),
    )
    return text


def append_file_change_summary_line(
    text: Text,
    palette: ThemePalette,
    label: str,
    additions: int | None,
    deletions: int | None,
) -> None:
    additions_text = "+-" if additions is None else f"+{additions}"
    deletions_text = "--" if deletions is None else f"-{deletions}"
    text.append("  - ", style=make_style(palette.muted))
    text.append(label, style=make_style(palette.foreground))
    text.append("  ", style=make_style(palette.muted))
    text.append(
        additions_text,
        style=make_change_stat_style(palette, additions, positive=True),
    )
    text.append(" ", style=make_style(palette.muted))
    text.append(
        deletions_text,
        style=make_change_stat_style(palette, deletions, positive=False),
    )


def build_repo_stats_sub_title(context: RepoContext, scanned_file_count: int) -> str:
    return (
        f"{context.repo_commit_count} commits · "
        f"{len(context.branches)} branches · "
        f"{scanned_file_count} files"
    )
