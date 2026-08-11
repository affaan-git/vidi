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


def make_change_stat_style(
    palette: ThemePalette,
    value: int | None,
    *,
    positive: bool,
) -> Style:
    if value is None or value == 0:
        return make_style(palette.muted)
    return make_style(palette.success if positive else palette.error, bold=True)


APP_THEME_NAME = "vidi"


def build_app_theme(
    source_theme: Theme,
    name: str = APP_THEME_NAME,
    *,
    transparent: bool = False,
) -> Theme:
    """Build a Textual theme whose variables equal the app palette.

    The app derives a ThemePalette by blending from a base theme and then paints
    Rich renderables with it. Registering that same palette as a Textual theme
    makes CSS $vars, default widget styles (table cursor, option-list highlight,
    scrollbars, input borders), and the programmatic palette all resolve to one
    set of colors instead of diverging from Textual's stock theme.

    When transparent, the background surfaces use the terminal's default color
    (ansi_default) so a translucent terminal shows through, while borders,
    selection, and text keep the palette's explicit colors.
    """
    palette = resolve_theme_palette(source_theme)
    variables = {
        # Neutral borders match the panels; focused borders come from CSS.
        "border": palette.border.hex,
        "border-blurred": palette.border.hex,
        # One selection color for the table cursor and the option-list.
        "block-cursor-background": palette.selected_bg.hex,
        "block-cursor-foreground": palette.selected_fg.hex,
        "block-cursor-text-style": "none",
        "block-cursor-blurred-background": palette.selected_bg.hex,
        "block-cursor-blurred-foreground": palette.selected_fg.hex,
        "block-cursor-blurred-text-style": "none",
        # Scrollbars tinted from the palette instead of the stock theme.
        "scrollbar": palette.surface.blend(palette.foreground, 0.22).hex,
        "scrollbar-hover": palette.surface.blend(palette.foreground, 0.32).hex,
        "scrollbar-active": palette.primary.hex,
        "scrollbar-background": palette.panel.hex,
        "scrollbar-background-hover": palette.panel.hex,
        "scrollbar-background-active": palette.panel.hex,
    }
    if transparent:
        # Only the content surfaces pass the terminal through. The scrollbar
        # track stays an opaque palette colour: an ansi_default track renders
        # transparent below the thumb, showing whatever sits behind the modal --
        # a stray dark segment that reads as a rendering glitch.
        variables.update(
            {
                "background": "ansi_default",
                "surface": "ansi_default",
                "panel": "ansi_default",
            }
        )
    return Theme(
        name=name,
        primary=palette.primary.hex,
        secondary=source_theme.secondary,
        accent=source_theme.accent,
        warning=source_theme.warning,
        success=palette.success.hex,
        error=palette.error.hex,
        foreground=palette.foreground.hex,
        background=palette.background.hex,
        surface=palette.surface.hex,
        panel=palette.panel.hex,
        dark=source_theme.dark,
        variables=variables,
    )


# Extra selectable themes, ported from the user's dotfiles Ghostty schemes
# (github.com/affaan-git/dotfiles). One Dark ANSI colors over two backgrounds.
DOTFILES_THEMES: dict[str, dict[str, str]] = {
    "pro-black": {
        "background": "#141619",
        "foreground": "#c9cdd6",
        "primary": "#61afef",
        "secondary": "#c678dd",
        "accent": "#56b6c2",
        "success": "#98c379",
        "warning": "#d19a66",
        "error": "#e06c75",
    },
    "one-night": {
        "background": "#282c3c",
        "foreground": "#cccccc",
        "primary": "#61afef",
        "secondary": "#c678dd",
        "accent": "#56b6c2",
        "success": "#98c379",
        "warning": "#d19a66",
        "error": "#e06c75",
    },
}


def _base_theme(name: str, colors: dict[str, str]) -> Theme:
    return Theme(
        name=name,
        primary=colors["primary"],
        secondary=colors.get("secondary"),
        accent=colors.get("accent"),
        warning=colors.get("warning"),
        success=colors["success"],
        error=colors["error"],
        foreground=colors["foreground"],
        background=colors["background"],
        dark=True,
    )


def app_themes(default_source: Theme, *, transparent: bool = False) -> list[Theme]:
    """Every theme the app registers: the default (built from Textual's current
    theme) plus the dotfiles themes, each fully configured with the same
    palette-derived variables so they all render consistently."""
    themes = [build_app_theme(default_source, name=APP_THEME_NAME, transparent=transparent)]
    themes.extend(
        build_app_theme(_base_theme(name, colors), name=name, transparent=transparent)
        for name, colors in DOTFILES_THEMES.items()
    )
    return themes


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
    # Keep the selection legible: on low-gamut themes (e.g. ansi) the blend can
    # collapse toward the panel, so nudge it toward the foreground if it lands
    # too close in brightness.
    if abs(selected_bg.brightness - panel.brightness) < 0.12:
        selected_bg = selected_bg.blend(foreground, 0.30)
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
