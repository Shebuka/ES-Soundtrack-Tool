"""Export assignments in the project's audio format."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from . import systemmap
from .dataset import Dataset
from .models import Project, Soundtrack, Track

logger = logging.getLogger(__name__)

DEFAULT_MAX_TRACK_SECONDS: float = 60.0
"""Fallback preview length when the project carries no ``max_track_seconds``."""


class AudioFormat(str, Enum):
    """Container an export writes.

    EmulationStation themes wire ``bgsound`` to a literal path, so the extension
    is part of the theme contract: picking a format means the theme must point
    at ``${system.theme}.<extension>``.
    """

    MP3 = "mp3"
    OGG = "ogg"
    WAV = "wav"

    @property
    def extension(self) -> str:
        return self.value

    @property
    def label(self) -> str:
        return _AUDIO_FORMAT_LABELS[self]


_AUDIO_FORMAT_LABELS: dict[AudioFormat, str] = {
    AudioFormat.MP3: "MP3 (tagged, recommended)",
    AudioFormat.OGG: "OGG (Ogg Vorbis)",
    AudioFormat.WAV: "WAV (uncompressed)",
}

AUDIO_FORMATS: tuple[AudioFormat, ...] = tuple(_AUDIO_FORMAT_LABELS)
"""Formats in display order, MP3 first."""

DEFAULT_AUDIO_FORMAT: AudioFormat = AudioFormat.MP3
"""Used when the project carries no (or an unusable) ``audio_format``."""


def resolve_audio_format(project: Project) -> AudioFormat:
    """Export format from ``project.output_settings``, defaulting to MP3."""
    raw = project.output_settings.get("audio_format")
    if raw is None:
        return DEFAULT_AUDIO_FORMAT
    try:
        return AudioFormat(str(raw).strip().lower())
    except ValueError:
        logger.warning(
            "export: ignoring unknown audio_format %r — using %s",
            raw,
            DEFAULT_AUDIO_FORMAT.value,
        )
        return DEFAULT_AUDIO_FORMAT


@dataclass(frozen=True, slots=True)
class ExportItem:
    """One ``theme_name -> track`` pair plus the tags its file must carry."""

    theme_name: str
    track: Track
    soundtrack_title: str

    @property
    def rttid(self) -> str:
        return self.track.rttid

    def tags(self) -> dict[str, str]:
        """Tag fields to write on the exported file.

        Batocera and other ES builds read title/artist from ID3 tags only, and
        Retrotracks MP3s are frequently untagged, so the values come from the
        dataset rather than from the downloaded source. OGG maps them to Vorbis
        comments, WAV to a RIFF INFO chunk.
        """
        title = self.track.title.strip()
        artist = (self.track.composer or self.soundtrack_title).strip()
        album = self.soundtrack_title.strip()
        fields = {"title": title, "artist": artist, "album": album}
        return {key: value for key, value in fields.items() if value}


def resolve_export_items(project: Project, dataset: Dataset) -> list[ExportItem]:
    """Map each assignment to the dataset track it should export."""
    soundtracks: dict[str, Soundtrack] = {soundtrack.url: soundtrack for soundtrack in dataset.soundtracks}
    resolved: list[ExportItem] = []
    for assignment in project.assignments:
        soundtrack = soundtracks.get(assignment.soundtrack_url)
        if soundtrack is None:
            continue
        track = next(
            (candidate for candidate in soundtrack.tracks if candidate.rttid == assignment.track_rttid),
            None,
        )
        if track is None:
            continue
        resolved.append(
            ExportItem(
                theme_name=assignment.theme_name,
                track=track,
                soundtrack_title=soundtrack.title,
            )
        )
    return resolved


def max_track_seconds(project: Project) -> float | None:
    """Preview length in seconds from ``project.output_settings``.

    A missing key falls back to :data:`DEFAULT_MAX_TRACK_SECONDS`; ``None`` or a
    non-positive value disables the cut so the full track is exported.
    """
    raw = project.output_settings.get("max_track_seconds", DEFAULT_MAX_TRACK_SECONDS)
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("export: ignoring malformed max_track_seconds %r", raw)
        return DEFAULT_MAX_TRACK_SECONDS
    return value if value > 0 else None


def apply_alias_copies(
    output_dir: Path | str,
    audio_format: AudioFormat = DEFAULT_AUDIO_FORMAT,
) -> list[Path]:
    """Copy canonical output files to their regional aliases."""
    output_dir = Path(output_dir)
    written: list[Path] = []
    for canonical, aliases in systemmap.alias_targets().items():
        source = output_dir / f"{canonical}.{audio_format.extension}"
        if not source.exists():
            continue
        for alias in aliases:
            dest = output_dir / f"{alias}.{audio_format.extension}"
            shutil.copy2(source, dest)
            written.append(dest)
    return written


def export(
    project: Project,
    dataset: Dataset,
    output_dir: Path | str,
    *,
    alias_copy: bool = False,
    cache_dir: Path | str | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> list[Path]:
    """Download + transcode every assignment, then optionally alias-copy.

    The actual download is delegated to ``es_ost.downloader``; this module stays
    importable without network access. ``cache_dir`` is forwarded so source MP3s
    are downloaded once and reused by preview playback and later exports.

    ``progress`` receives ``(position, total, theme_name)`` before each track is
    processed, plus an initial ``(0, total, "")`` so a caller can size a progress
    bar deterministically without knowing the assignment count up front.
    """
    from .. import downloader

    output_dir = Path(output_dir)
    resolved = resolve_export_items(project, dataset)
    max_seconds = max_track_seconds(project)
    audio_format = resolve_audio_format(project)
    logger.info(
        "export: exporting %d assignments to %s (format=%s, max_track_seconds=%s, cache=%s)",
        len(resolved),
        output_dir,
        audio_format.value,
        max_seconds,
        cache_dir or downloader.default_cache_dir(),
    )
    if progress is not None:
        progress(0, len(resolved), "")
    written: list[Path] = []
    for position, item in enumerate(resolved, start=1):
        if progress is not None:
            progress(position, len(resolved), item.theme_name)
        logger.info(
            "export: [%d/%d] %s (rttid=%s)",
            position,
            len(resolved),
            item.theme_name,
            item.rttid,
        )
        written.append(
            downloader.download_and_transcode(
                item.rttid,
                output_dir / f"{item.theme_name}.{audio_format.extension}",
                audio_format=audio_format.value,
                tags=item.tags(),
                max_seconds=max_seconds,
                cache_dir=cache_dir,
            )
        )
    if alias_copy:
        written.extend(apply_alias_copies(output_dir, audio_format))
    logger.info("export: wrote %d files", len(written))
    return written
