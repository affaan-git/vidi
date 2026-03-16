"""Theme helpers."""

from __future__ import annotations

from rich.style import Style
from textual.color import Color
from textual.theme import Theme

from .models import ThemePalette


def make_style(
    foreground: Color | None = None,
    *,
    background: Color | None = None,
    bold: bool = False,
    dim: bool = False,
) -> Style:
    return Style(
        color=foreground.rich_color if foreground is not None else None,
        bgcolor=background.rich_color if background is not None else None,
        bold=bold,
        dim=dim,
    )


def resolve_theme_palette(theme: Theme) -> ThemePalette:
    primary = Color.parse(theme.primary)
    success = Color.parse(theme.success)
    error = Color.parse(theme.error)

    if theme.foreground is not None:
        foreground = Color.parse(theme.foreground)
    elif theme.background is not None:
        foreground = Color.parse(theme.background).get_contrast_text()
    else:
        foreground = primary.get_contrast_text()

    if theme.background is not None:
        background = Color.parse(theme.background)
    elif theme.dark:
        background = foreground.blend(primary, 0.08).darken(0.82)
    else:
        background = foreground.get_contrast_text().blend(primary, 0.05)

    if theme.panel is not None:
        panel = Color.parse(theme.panel)
    elif theme.surface is not None:
        source = Color.parse(theme.surface)
        panel = source.lighten(0.02) if theme.dark else source.darken(0.03)
    else:
        panel = background.lighten(0.06) if theme.dark else background.darken(0.06)

    if theme.surface is not None:
        surface = Color.parse(theme.surface)
    elif theme.panel is not None:
        source = Color.parse(theme.panel)
        surface = source.darken(0.02) if theme.dark else source.lighten(0.03)
    else:
        surface = background.lighten(0.10) if theme.dark else background.darken(0.03)

    muted = foreground.blend(panel, 0.50 if theme.dark else 0.35)
    border = panel.blend(foreground, 0.18 if theme.dark else 0.35)
    selected_bg = primary.blend(surface, 0.42 if theme.dark else 0.60)
    selected_fg = selected_bg.get_contrast_text()
    overlay = background.darken(0.35 if theme.dark else 0.75).with_alpha(
        0.68 if theme.dark else 0.50
    )

    return ThemePalette(
        background=background,
        panel=panel,
        surface=surface,
        foreground=foreground,
        success=success,
        error=error,
        muted=muted,
        border=border,
        primary=primary,
        selected_bg=selected_bg,
        selected_fg=selected_fg,
        overlay=overlay,
    )
