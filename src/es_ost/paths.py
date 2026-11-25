"""Filesystem locations shared by a source checkout and a frozen build.

The bundled data/ directory, the ffmpeg binary and (in a packaged build) the
embedded .env all live next to the executable, while a checkout keeps them at
the repository root. Nuitka, PyInstaller and a plain interpreter each report
that root differently, so resolve it once here instead of using __file__
arithmetic in every module.
"""

from __future__ import annotations

import sys
from pathlib import Path


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
