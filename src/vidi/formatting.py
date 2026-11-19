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


def change_stat_labels(additions: int | None, deletions: int | None) -> tuple[str, str]:
    """The "+adds" and "-dels" labels, with "+-" and "--" for a binary file."""
    return (
        "+-" if additions is None else f"+{additions}",
        "--" if deletions is None else f"-{deletions}",
    )


def append_file_change_summary_line(
    text: Text,
    palette: ThemePalette,
    label: str,
    additions: int | None,
    deletions: int | None,
) -> None:
    additions_text, deletions_text = change_stat_labels(additions, deletions)
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


def _count(n: int, singular: str, plural: str | None = None) -> str:
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


def build_repo_stats_sub_title(
    context: RepoContext,
    scanned_file_count: int,
    branch: str | None = None,
    ahead: int = 0,
    behind: int = 0,
) -> str:
    parts: list[str] = []
    if branch:
        marks = ""
        if ahead:
            marks += f"  ↑{ahead}"
        if behind:
            marks += f"  ↓{behind}"
        parts.append(f"{branch}{marks}")
    parts.append(_count(context.repo_commit_count, "commit"))
    parts.append(_count(len(context.branches), "branch", "branches"))
    parts.append(_count(scanned_file_count, "file"))
    return "  ·  ".join(parts)
