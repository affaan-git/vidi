"""User preferences, persisted to a small JSON file under the config dir."""

from __future__ import annotations

import json
import os
from pathlib import Path

_config_home = os.environ.get("XDG_CONFIG_HOME")
CONFIG_DIR = (Path(_config_home) if _config_home else Path.home() / ".config") / "vidi"
CONFIG_PATH = CONFIG_DIR / "config.json"


def _load() -> dict:
    try:
        data = json.loads(CONFIG_PATH.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _set_many(values: dict[str, object]) -> None:
    """Persist preferences, ignoring write failures."""
    try:
        prefs = _load()
        prefs.update(values)
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(prefs, indent=2) + "\n")
    except OSError:
        pass


def _set(key: str, value: object) -> None:
    _set_many({key: value})


def load_theme() -> str | None:
    """The persisted theme name, or None if nothing was saved."""
    value = _load().get("theme")
    return value if isinstance(value, str) else None


def save_theme(name: str) -> None:
    _set("theme", name)


def load_graph_browse() -> bool:
    """Whether the graph waits for Enter to select a day."""
    return bool(_load().get("graph_browse", False))


def save_graph_browse(value: bool) -> None:
    _set("graph_browse", bool(value))


def load_transparent_background() -> bool:
    """Whether the app uses the terminal's background."""
    return bool(_load().get("transparent_background", False))


def save_transparent_background(value: bool) -> None:
    _set("transparent_background", bool(value))


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def load_language_settings() -> dict:
    """The persisted language-scan settings, falling back to sensible defaults."""
    prefs = _load()
    mode = prefs.get("language_mode")
    return {
        "language_mode": mode if mode in ("bytes", "files") else "bytes",
        "exclude_languages": _str_list(prefs.get("exclude_languages")),
        "ignore_dirs": _str_list(prefs.get("ignore_dirs")),
    }


def save_language_settings(
    language_mode: str, exclude_languages: list[str], ignore_dirs: list[str]
) -> None:
    _set_many(
        {
            "language_mode": language_mode if language_mode in ("bytes", "files") else "bytes",
            "exclude_languages": list(exclude_languages),
            "ignore_dirs": list(ignore_dirs),
        }
    )


def reset() -> None:
    """Forget saved preferences."""
    try:
        CONFIG_PATH.unlink()
    except OSError:
        pass
