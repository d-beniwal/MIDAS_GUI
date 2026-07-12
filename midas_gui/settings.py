"""Per-user configuration for the GUI (pure stdlib — no Qt, no constants).

Two layers, higher wins:

    per-user file  →  built-in constants

The built-in layer lives in :mod:`midas_gui.constants`, which applies the config
returned by :func:`load_config` to its own module globals at import time.  This
module imports nothing from the package (no import cycle; works before a
``QApplication`` exists).  Config files are JSON; a missing or malformed file is
ignored rather than fatal.  Sharing between users is done by exporting/importing a
JSON file (Preferences ▸ Save/Load), not by any shared-file mechanism here.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

APP_DIRNAME = "midas_gui"
CONFIG_FILENAME = "config.json"

_cache: Optional[dict] = None


# ── location ─────────────────────────────────────────────────────────────────
def user_config_path() -> Path:
    """Per-user config path, chosen per-OS without any Qt dependency."""
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / APP_DIRNAME / CONFIG_FILENAME


# ── io ───────────────────────────────────────────────────────────────────────
def read_json(path) -> dict:
    """Read a JSON dict; return {} on any problem (missing / malformed / non-dict)."""
    try:
        p = Path(path)
        if p.is_file():
            data = json.loads(p.read_text())
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def load_config(*, force: bool = False) -> dict:
    """The per-user config dict (cached; never raises; {} if no file)."""
    global _cache
    if _cache is not None and not force:
        return _cache
    _cache = read_json(user_config_path())
    return _cache


def reload() -> dict:
    """Re-read the config file from disk and refresh the cache."""
    return load_config(force=True)


def save_user_config(cfg: dict) -> Path:
    """Write ``cfg`` to the per-user config file (creating parent dirs) and
    invalidate the cache. Returns the path written."""
    global _cache
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2, sort_keys=False) + "\n")
    _cache = None
    return path


def reset_user_config() -> None:
    """Delete the per-user config file (return to built-in shipped defaults)."""
    global _cache
    try:
        p = user_config_path()
        if p.is_file():
            p.unlink()
    finally:
        _cache = None


def config_paths() -> dict:
    up = user_config_path()
    return {"user": up, "user_exists": up.is_file()}
