"""Filesystem locations shared by a source checkout and a frozen build.

The bundled data/ directory, the ffmpeg binary and (in a packaged build) the
embedded .env all live next to the executable, while a checkout keeps them at
the repository root. Nuitka, PyInstaller and a plain interpreter each report
that root differently, so resolve it once here instead of using __file__
arithmetic in every module.

The per-user locations (log directory, track cache) live here too, so the two
cannot drift apart into differently named folders.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "ES Soundtrack Tool"
"""Per-user folder name, shared by the log and the downloaded-track cache."""

APP_SLUG = "es-soundtrack-tool"
"""Lowercase spelling for the XDG locations, which use dashes by convention."""


def is_frozen() -> bool:
    """True when running from a Nuitka or PyInstaller bundle."""
    if getattr(sys, "frozen", False):
        return True
    return "__compiled__" in globals()


def app_root() -> Path:
    """Return the directory that holds data/ and assets/."""
    if getattr(sys, "frozen", False):
        # PyInstaller onefile unpacks to _MEIPASS; a frozen onedir build keeps
        # its resources next to the executable.
        bundle = getattr(sys, "_MEIPASS", None)
        return Path(bundle).resolve() if bundle else Path(sys.executable).resolve().parent
    if "__compiled__" in globals():
        # Nuitka standalone: the data dirs are copied next to the binary (inside
        # Contents/MacOS for a macOS .app bundle).
        return Path(sys.executable).resolve().parent
    # src/es_ost/paths.py -> repository root.
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """The frozen dataset directory."""
    return app_root() / "data"


def assets_dir() -> Path:
    """The assets directory bundled into a release."""
    return app_root() / "assets"


def ffmpeg_dir() -> Path:
    """Directory holding the bundled ffmpeg binary, if present."""
    return assets_dir() / "ffmpeg"


def _local_appdata() -> Path:
    """%LOCALAPPDATA%, or the conventional fallback when it is not set."""
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")


def user_log_dir() -> Path:
    """Per-user directory for ``app.log``, created on demand.

    A packaged app may be launched from / or from a read-only volume, so the log
    cannot live in the working directory.
    """
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Logs" / APP_DIR_NAME
    elif sys.platform == "win32":
        base = _local_appdata() / APP_DIR_NAME
    else:
        state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
        base = Path(state) / APP_SLUG
    base.mkdir(parents=True, exist_ok=True)
    return base


def user_cache_root() -> Path:
    """Per-user root for the downloaded-track cache.

    Windows has no cache directory of its own, so this is the same folder as the
    log: one "ES Soundtrack Tool" to look in rather than two spellings of the
    same name. macOS and Linux keep the conventional Logs/State versus Caches
    split.
    """
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / APP_DIR_NAME
    if sys.platform == "win32":
        return _local_appdata() / APP_DIR_NAME
    cache = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(cache) / APP_SLUG
