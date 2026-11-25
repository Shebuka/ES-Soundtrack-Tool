"""Load the application environment (.env) for dev or packaged builds.

Threat model: the registered ScreenScraper developer credentials
(``devid``/``devpassword``/``softname``) are a shared, non-rotatable,
non-scopable client token. They cannot be secured client-side — anyone can
recover them via a network proxy. The goal is only to (a) hide them from
casual inspection and (b) keep them out of version control and immutable
artifacts. This is obfuscation, not security.

Dev builds load a root ``.env`` file with python-dotenv's default search (no
hardcoded path). Packaged builds read the same file from the Qt resource
system, where ``resource_data.qrc`` embeds it as ``:/data/.env``. Qt is
imported lazily so the data layer stays Qt-free.
"""

from __future__ import annotations

import io

from dotenv import load_dotenv

from ..paths import is_frozen
from .credentials import DevCredentials, dev_credentials_from_env

PACKAGED_ENV_RESOURCE = ":/data/.env"


class EnvironmentLoadError(RuntimeError):
    """Raised when the packaged .env resource cannot be read."""


def _read_resource_text(resource: str) -> str:
    try:
        from PySide6.QtCore import QFile, QIODevice  # type: ignore[import-not-found]
    except ImportError as exc:
        raise EnvironmentLoadError("Qt is required to read the embedded .env") from exc

    file = QFile(resource)
    if not file.open(QIODevice.OpenModeFlag.ReadOnly | QIODevice.OpenModeFlag.Text):
        raise EnvironmentLoadError(f"failed to open embedded resource {resource}")
    try:
        return file.readAll().toStdString()
    finally:
        file.close()


def load_environment() -> None:
    """Load the root .env into os.environ from the correct source."""
    if not is_frozen():
        load_dotenv()  # python-dotenv default: searches the directory tree for .env
        return
    load_dotenv(stream=io.StringIO(_read_resource_text(PACKAGED_ENV_RESOURCE)))


def load_dev_credentials() -> DevCredentials:
    """Load the app's developer credentials from the environment."""
    load_environment()
    return dev_credentials_from_env()
