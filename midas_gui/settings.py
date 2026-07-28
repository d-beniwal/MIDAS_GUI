"""Per-user configuration for the GUI (pure stdlib — no Qt, no constants).

Two layers, higher wins:

    active profile file  →  built-in constants

The built-in layer lives in :mod:`midas_gui.constants`, which applies the config
returned by :func:`load_config` to its own module globals at import time (and,
on a profile switch, via :func:`midas_gui.constants.reload_from_config`). This
module imports nothing from the package (no import cycle; works before a
``QApplication`` exists). Config files are JSON; a missing or malformed file is
ignored rather than fatal.

Profiles let a user keep several named sets of defaults (e.g. per beamline) and
switch between them from Preferences ▸ Profile. On disk::

    <config dir>/
        profile_meta.json      {"active": "<name>"}
        profiles/
            Default.json
            <name>.json ...

A user upgrading from a version with no profile support has their old single
``config.json`` transparently migrated into ``profiles/Default.json`` the first
time this module touches the filesystem — see :func:`_ensure_profiles`.

Sharing between users is done by exporting/importing a JSON file
(Preferences ▸ Save/Load config), not by any shared-file mechanism here.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

APP_DIRNAME = "midas_gui"
CONFIG_FILENAME = "config.json"          # legacy single-profile file (pre-migration)
PROFILES_DIRNAME = "profiles"
META_FILENAME = "profile_meta.json"
DEFAULT_PROFILE = "Default"

_cache: Optional[dict] = None


# ── location ─────────────────────────────────────────────────────────────────
def _config_base_dir() -> Path:
    """Per-OS config base directory, chosen without any Qt dependency."""
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / APP_DIRNAME


def _profiles_dir() -> Path:
    return _config_base_dir() / PROFILES_DIRNAME


def _meta_path() -> Path:
    return _config_base_dir() / META_FILENAME


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


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")


# ── profile migration / bookkeeping ───────────────────────────────────────────
def _ensure_profiles() -> None:
    """Idempotent, cheap-to-repeat setup: make sure ``profiles/`` and the meta
    file exist, migrating a pre-existing single ``config.json`` into a
    ``Default`` profile the first time this runs."""
    pdir = _profiles_dir()
    legacy = _config_base_dir() / CONFIG_FILENAME
    if not pdir.is_dir():
        pdir.mkdir(parents=True, exist_ok=True)
        seed = read_json(legacy) if legacy.is_file() else {}
        _write_json(pdir / f"{DEFAULT_PROFILE}.json", seed)
    elif not any(pdir.glob("*.json")):
        # Directory exists but every profile was removed by hand — reseed.
        _write_json(pdir / f"{DEFAULT_PROFILE}.json", {})

    meta = _meta_path()
    names = _profile_names()
    if not meta.is_file():
        _write_json(meta, {"active": names[0] if names else DEFAULT_PROFILE})
    elif read_json(meta).get("active") not in names:
        _write_json(meta, {"active": names[0] if names else DEFAULT_PROFILE})


def _profile_names() -> list:
    return sorted(p.stem for p in _profiles_dir().glob("*.json"))


_NAME_RE = re.compile(r"^[^/\\:*?\"<>|]{1,80}$")


def _validate_profile_name(name: str) -> str:
    name = (name or "").strip()
    if not name or not _NAME_RE.match(name):
        raise ValueError(
            "Profile name must be 1-80 characters and may not contain "
            '/ \\ : * ? " < > |')
    return name


# ── profile API ────────────────────────────────────────────────────────────
def list_profiles() -> list:
    """Names of all saved profiles, sorted alphabetically."""
    _ensure_profiles()
    return _profile_names()


def active_profile() -> str:
    """Name of the currently active profile (auto-recovers if it went missing)."""
    _ensure_profiles()
    return read_json(_meta_path()).get("active", DEFAULT_PROFILE)


def set_active_profile(name: str) -> None:
    """Switch the active profile. Raises ValueError if it doesn't exist."""
    global _cache
    _ensure_profiles()
    if name not in _profile_names():
        raise ValueError(f"No such profile: {name!r}")
    _write_json(_meta_path(), {"active": name})
    _cache = None


def profile_path(name: Optional[str] = None) -> Path:
    """Path to a profile's JSON file (the active one, by default)."""
    _ensure_profiles()
    return _profiles_dir() / f"{(name or active_profile())}.json"


def create_profile(name: str, seed_cfg: Optional[dict] = None) -> Path:
    """Create a new, empty (or pre-seeded) profile. Raises ValueError if the
    name is invalid or already taken."""
    name = _validate_profile_name(name)
    _ensure_profiles()
    if name in _profile_names():
        raise ValueError(f"A profile named {name!r} already exists")
    path = _profiles_dir() / f"{name}.json"
    _write_json(path, dict(seed_cfg or {}))
    return path


def duplicate_profile(src: str, dst: str) -> Path:
    """Copy an existing profile's contents into a new profile named ``dst``."""
    cfg = read_json(profile_path(src))
    return create_profile(dst, seed_cfg=cfg)


def rename_profile(old: str, new: str) -> Path:
    """Rename a profile, keeping it active if it was the active one."""
    global _cache
    new = _validate_profile_name(new)
    _ensure_profiles()
    names = _profile_names()
    if old not in names:
        raise ValueError(f"No such profile: {old!r}")
    if new == old:
        return profile_path(old)
    if new in names:
        raise ValueError(f"A profile named {new!r} already exists")
    was_active = active_profile() == old
    old_path, new_path = profile_path(old), _profiles_dir() / f"{new}.json"
    old_path.rename(new_path)
    if was_active:
        _write_json(_meta_path(), {"active": new})
        _cache = None
    return new_path


def delete_profile(name: str) -> None:
    """Delete a profile. Refuses to delete the last remaining profile. If the
    deleted profile was active, the active pointer moves to another
    remaining profile (``Default`` if present, else whichever sorts first)."""
    global _cache
    _ensure_profiles()
    names = _profile_names()
    if name not in names:
        raise ValueError(f"No such profile: {name!r}")
    if len(names) <= 1:
        raise ValueError("Cannot delete the only remaining profile")
    was_active = active_profile() == name
    profile_path(name).unlink(missing_ok=True)
    if was_active:
        remaining = _profile_names()
        fallback = DEFAULT_PROFILE if DEFAULT_PROFILE in remaining else remaining[0]
        _write_json(_meta_path(), {"active": fallback})
        _cache = None


# ── active-profile config (unchanged call signatures) ────────────────────────
def user_config_path() -> Path:
    """Path to the *active profile's* config file."""
    return profile_path()


def load_config(*, force: bool = False) -> dict:
    """The active profile's config dict (cached; never raises; {} if no file)."""
    global _cache
    if _cache is not None and not force:
        return _cache
    _cache = read_json(user_config_path())
    return _cache


def reload() -> dict:
    """Re-read the active profile's config file from disk and refresh the cache."""
    return load_config(force=True)


def save_user_config(cfg: dict) -> Path:
    """Write ``cfg`` to the active profile's config file (creating parent dirs)
    and invalidate the cache. Returns the path written."""
    global _cache
    path = user_config_path()
    _write_json(path, cfg)
    _cache = None
    return path


def reset_user_config() -> None:
    """Reset the active profile to the built-in shipped defaults (an empty
    overlay) without removing the profile itself."""
    save_user_config({})


def config_paths() -> dict:
    up = user_config_path()
    return {"user": up, "user_exists": up.is_file()}
