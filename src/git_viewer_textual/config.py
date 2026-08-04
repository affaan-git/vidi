"""User preferences, persisted to a small JSON file under the config dir."""

from __future__ import annotations

import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "git-viewer-textual"
CONFIG_PATH = CONFIG_DIR / "config.json"


def _load() -> dict[str, str]:
    try:
        data = json.loads(CONFIG_PATH.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_theme() -> str | None:
    """The persisted theme name, or None if nothing was saved."""
    value = _load().get("theme")
    return value if isinstance(value, str) else None


def save_theme(name: str) -> None:
    """Persist the chosen theme; failures are ignored (a viewer, not a vault)."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        prefs = _load()
        prefs["theme"] = name
        CONFIG_PATH.write_text(json.dumps(prefs, indent=2) + "\n")
    except OSError:
        pass


def reset() -> None:
    """Forget saved preferences so the app falls back to its defaults."""
    try:
        CONFIG_PATH.unlink()
    except OSError:
        pass
