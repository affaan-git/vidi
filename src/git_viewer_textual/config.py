"""User preferences, persisted to a small JSON file under the config dir."""

from __future__ import annotations

import json
import os
from pathlib import Path

_config_home = os.environ.get("XDG_CONFIG_HOME")
CONFIG_DIR = (Path(_config_home) if _config_home else Path.home() / ".config") / "git-viewer-textual"
CONFIG_PATH = CONFIG_DIR / "config.json"


def _load() -> dict:
    try:
        data = json.loads(CONFIG_PATH.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _set(key: str, value: object) -> None:
    """Persist one preference; failures are ignored (a viewer, not a vault)."""
    try:
        prefs = _load()
        prefs[key] = value
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(prefs, indent=2) + "\n")
    except OSError:
        pass


def load_theme() -> str | None:
    """The persisted theme name, or None if nothing was saved."""
    value = _load().get("theme")
    return value if isinstance(value, str) else None


def save_theme(name: str) -> None:
    _set("theme", name)


def load_graph_browse() -> bool:
    """Whether the graph browses on move (select on Enter) instead of selecting
    as you move."""
    return bool(_load().get("graph_browse", False))


def save_graph_browse(value: bool) -> None:
    _set("graph_browse", bool(value))


def load_transparent_background() -> bool:
    """Whether the app uses the terminal's background (so translucency shows)."""
    return bool(_load().get("transparent_background", False))


def save_transparent_background(value: bool) -> None:
    _set("transparent_background", bool(value))


def reset() -> None:
    """Forget saved preferences so the app falls back to its defaults."""
    try:
        CONFIG_PATH.unlink()
    except OSError:
        pass
