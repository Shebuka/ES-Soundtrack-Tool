"""Disk cache for scraper HTTP responses.

Both scrapers store raw response bodies as plain files in one cache directory,
keyed by a hash of the request URL. Entries are permanent — retro data is assumed
immutable — and only :func:`clear_cache` (exposed as ``es-ost-prep clear-cache``)
removes them.

Cache access is best-effort: a missing, unreadable or half-written entry counts
as a miss, and a failed write is logged instead of raised, so a broken cache can
never turn a working scrape into a failing one.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


def cache_key(url: str, suffix: str) -> str:
    """Return the cache filename for ``url`` as ``<sha1>.<suffix>``."""
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return f"{digest}.{suffix}"


class PageCache:
    """A best-effort text cache rooted at a single directory.

    Passing ``directory=None`` disables caching, so callers never have to branch
    on whether a cache exists.
    """

    def __init__(self, directory: Path | str | None = None) -> None:
        self._dir = Path(directory) if directory else None
        if self._dir is not None:
            self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return self._dir is not None

    @property
    def directory(self) -> Path | None:
        return self._dir

    def path(self, name: str) -> Path | None:
        """Return the on-disk path for ``name``, or ``None`` when disabled."""
        return self._dir / name if self._dir is not None else None

    def get(self, name: str) -> str | None:
        """Return the cached body for ``name``, or ``None`` on a miss."""
        path = self.path(name)
        if path is None or not path.is_file():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("cache: ignoring unreadable entry %s: %s", path, exc)
            return None

    def put(self, name: str, body: str) -> None:
        """Store ``body`` under ``name``, atomically and without raising."""
        path = self.path(name)
        if path is None:
            return
        tmp_path = path.with_name(f"{path.name}.tmp")
        try:
            tmp_path.write_text(body, encoding="utf-8")
            os.replace(tmp_path, path)
        except OSError as exc:
            logger.warning("cache: could not store %s: %s", path, exc)
            tmp_path.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class CacheClearResult:
    """Outcome of a cache wipe; ``freed_bytes`` counts regular files only."""

    removed: int = 0
    freed_bytes: int = 0

    def __str__(self) -> str:
        return f"removed {self.removed} entries, freed {self.freed_bytes / 1024:.1f} KiB"


def clear_cache(directory: Path | str) -> CacheClearResult:
    """Delete every entry inside the cache ``directory``.

    A missing directory is a no-op. Individual failures are logged and skipped so
    one undeletable file does not abort the cleanup.
    """
    root = Path(directory)
    if not root.is_dir():
        logger.debug("cache: nothing to clear at %s", root)
        return CacheClearResult()

    removed = 0
    freed = 0
    for entry in sorted(root.iterdir()):
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                freed += entry.stat().st_size
                entry.unlink()
            removed += 1
        except OSError as exc:
            logger.warning("cache: could not remove %s: %s", entry, exc)

    result = CacheClearResult(removed, freed)
    logger.info("cache: cleared %s — %s", root, result)
    return result
