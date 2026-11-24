"""Fetch songs from Retrotracks and transcode to the chosen export format.

Uses a bundled ffmpeg/ffprobe binary when present, falling back to ``ffmpeg``
and ``ffprobe`` on PATH.

Exported tracks are loudness-normalized (EBU R128, -16 LUFS) and cut to at most
``max_seconds`` (60 s by default) with a short fade-out, mirroring the
``smart_preview.sh`` pipeline used for the other soundtrack sets. Batocera and
other ES builds read title/artist from ID3 tags only, so the tags are written
explicitly (ID3v2.3 plus an ID3v1 fallback) instead of relying on the source.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from collections.abc import Mapping
from pathlib import Path

from .prep.retrotracks import USER_AGENT, resolve_track, track_url

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

logger = logging.getLogger(__name__)

DEFAULT_MAX_SECONDS: float = 60.0
"""Default preview length, in seconds, applied to every exported track."""

FADE_SECONDS: float = 3.0
"""Length of the fade-out appended when a track is cut to the preview length."""

LOUDNESS_FILTER: str = "loudnorm=I=-16:TP=-1.5"
"""Single-pass EBU R128 normalization, as used by ``smart_preview.sh``."""

MP3_VBR_QUALITY: int = 4
"""LAME VBR quality, i.e. the ``-V4`` preset (ffmpeg spells it ``-q:a 4``).

VBR spends bits where the signal needs them instead of padding silence at a
fixed CBR rate; the Xing header ffmpeg writes keeps seeking and duration
reporting correct for VBR files.
"""

ID3V2_VERSION: int = 3
"""ID3v2.3, which Batocera's ``id3v2lib`` reads; ffmpeg defaults to 2.4."""

VORBIS_QUALITY: int = 4
"""``-q:a`` value used for the Ogg Vorbis encoder."""

WAV_SAMPLE_RATE: int = 44100
"""Pinned sample rate for WAV.

loudnorm internally resamples to 192 kHz, so an unpinned WAV preview would be
~46 MB per minute instead of ~10 MB.
"""

_ENCODER_ARGS: dict[str, list[str]] = {
    "mp3": [
        "-c:a",
        "libmp3lame",
        # LAME's -V4; ffmpeg exposes VBR quality through -q:a, not -V.
        "-q:a",
        str(MP3_VBR_QUALITY),
        # Batocera's id3v2lib reads 2.3; the v1 record is its fallback.
        "-id3v2_version",
        str(ID3V2_VERSION),
        "-write_id3v1",
        "1",
    ],
    "ogg": ["-c:a", "libvorbis", "-q:a", str(VORBIS_QUALITY)],
    "wav": ["-c:a", "pcm_s16le", "-ar", str(WAV_SAMPLE_RATE)],
}

AUDIO_FORMATS: tuple[str, ...] = tuple(_ENCODER_ARGS)
"""Formats this module can encode; mirrors ``core.output.AudioFormat``."""


def encoder_args(audio_format: str) -> list[str]:
    """ffmpeg output options for ``audio_format``.

    An unknown container is rejected here rather than handed to ffmpeg, which
    would otherwise fail only after the track had been downloaded.
    """
    try:
        return list(_ENCODER_ARGS[audio_format])
    except KeyError:
        raise ValueError(
            f"unsupported audio format {audio_format!r}; expected one of {sorted(_ENCODER_ARGS)}"
        ) from None


def default_cache_dir() -> Path:
    """Platform user-cache directory for downloaded track audio.

    ``ES_OST_CACHE_DIR`` overrides the location (useful for tests and portable
    installations). The cache lives outside the app bundle so a packaged build
    can be read-only.
    """
    override = os.environ.get("ES_OST_CACHE_DIR")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "es-soundtrack-tool" / "tracks"


def track_cache_path(rttid: str, cache_dir: Path | str) -> Path:
    """Return the deterministic cache path for a track's source MP3."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(rttid)).strip("._") or "track"
    return Path(cache_dir) / f"{safe}.mp3"


def is_cached(path: Path | str) -> bool:
    """A cache entry counts as present only when it is a non-empty regular file."""
    path = Path(path)
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def ensure_cached_mp3(
    rttid: str,
    cache_dir: Path | str,
    *,
    timeout: float = 60.0,
) -> Path:
    """Return the cached source MP3 for ``rttid``, downloading it on a miss.

    The download is written to a ``.part`` sibling and atomically renamed, so a
    failed or interrupted download never leaves a playable-looking half file.
    """
    path = track_cache_path(rttid, cache_dir)
    if is_cached(path):
        logger.debug("downloader: cache hit for rttid=%s at %s", rttid, path)
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.part")
    try:
        download_mp3(rttid, tmp_path, timeout=timeout)
        os.replace(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)
    logger.info("downloader: cached rttid=%s at %s", rttid, path)
    return path


def find_ffmpeg() -> str:
    """Locate the bundled ffmpeg binary, or fall back to PATH."""
    asset_roots = (
        _PROJECT_ROOT / "assets" / "ffmpeg",
        _PROJECT_ROOT / "assets",
    )
    for root in asset_roots:
        for name in ("ffmpeg", "ffmpeg.exe"):
            candidate = root / name
            if candidate.exists():
                return str(candidate)
    return shutil.which("ffmpeg") or "ffmpeg"


def find_ffprobe(*, ffmpeg: str | None = None) -> str:
    """Locate the bundled ffprobe binary, or fall back to PATH (or ffmpeg's sibling)."""
    candidates: list[Path] = []
    asset_roots = (
        _PROJECT_ROOT / "assets" / "ffmpeg",
        _PROJECT_ROOT / "assets",
    )
    for root in asset_roots:
        candidates.extend((root / "ffprobe", root / "ffprobe.exe"))
    if ffmpeg:
        # A bundled ffmpeg usually ships with its ffprobe next to it.
        bundled = Path(ffmpeg)
        candidates.append(bundled.with_name(f"ffprobe{bundled.suffix}"))
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return shutil.which("ffprobe") or "ffprobe"


def probe_duration(
    path: Path,
    *,
    ffprobe: str | None = None,
    timeout: float = 60.0,
) -> float | None:
    """Return the track length in seconds, or ``None`` when ffprobe cannot tell.

    A missing or broken ffprobe must not fail the export: the caller treats an
    unknown duration as "long enough to need the cut".
    """
    command = [
        ffprobe or find_ffprobe(),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("downloader: could not probe duration of %s: %s", path, exc)
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        logger.warning("downloader: unparsable duration %r for %s", result.stdout.strip(), path)
        return None


def build_transcode_command(
    source_path: Path,
    dest_path: Path,
    *,
    ffmpeg: str,
    audio_format: str = "mp3",
    duration: float | None = None,
    max_seconds: float | None = DEFAULT_MAX_SECONDS,
    normalize: bool = True,
    overwrite: bool = True,
    tags: Mapping[str, str] | None = None,
) -> list[str]:
    """Assemble the ffmpeg argv for a normalized, optionally trimmed file.

    ``audio_format`` selects the encoder options (see :func:`encoder_args`).
    ``duration`` is the probed source length; ``None`` means "unknown" and the
    track is treated as long enough to be cut. ``max_seconds`` of ``None``
    disables the cut (and therefore the fade-out) entirely.

    ``tags`` are written as explicit metadata after ``-map_metadata 0``, so a
    dataset-derived title/artist always wins: Retrotracks MP3s are often
    untagged, and Batocera ignores anything but ID3.
    """
    cut: float | None = None
    if max_seconds is not None and (duration is None or duration > max_seconds):
        cut = max_seconds

    filters: list[str] = []
    if normalize:
        filters.append(LOUDNESS_FILTER)
    if cut is not None:
        fade_start = max(cut - FADE_SECONDS, 0.0)
        filters.append(f"afade=t=out:st={fade_start:g}:d={FADE_SECONDS:g}")

    command = [
        ffmpeg,
        "-y" if overwrite else "-n",
        "-i",
        str(source_path),
        # Keep whatever the source carries, then override it with our tags.
        "-map_metadata",
        "0",
        "-vn",
        *encoder_args(audio_format),
    ]
    for key, value in sorted((tags or {}).items()):
        command += ["-metadata", f"{key}={value}"]
    if filters:
        command += ["-af", ",".join(filters)]
    if cut:
        command += ["-to", f"{max_seconds:g}"]
    command.append(str(dest_path))
    return command


def transcode_audio(
    source_path: Path,
    dest_path: Path,
    *,
    audio_format: str = "mp3",
    ffmpeg: str | None = None,
    ffprobe: str | None = None,
    overwrite: bool = True,
    max_seconds: float | None = DEFAULT_MAX_SECONDS,
    normalize: bool = True,
    tags: Mapping[str, str] | None = None,
) -> Path:
    """Transcode the cached source into ``audio_format``, cut to ``max_seconds``.

    Source metadata is kept (``-map_metadata 0``) and ``tags`` override it, so
    the file carries a title/artist for ES to display. Tracks longer than
    ``max_seconds`` are trimmed to that length with a ``FADE_SECONDS`` fade-out;
    shorter tracks are only normalized.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_bin = ffmpeg or find_ffmpeg()
    duration: float | None = None
    if max_seconds is not None:
        duration = probe_duration(source_path, ffprobe=ffprobe or find_ffprobe(ffmpeg=ffmpeg_bin))
    command = build_transcode_command(
        source_path,
        dest_path,
        ffmpeg=ffmpeg_bin,
        audio_format=audio_format,
        duration=duration,
        max_seconds=max_seconds,
        normalize=normalize,
        overwrite=overwrite,
        tags=tags,
    )
    logger.info(
        "downloader: transcoding %s -> %s (format=%s, duration=%s, max_seconds=%s, normalize=%s, tags=%s)",
        source_path,
        dest_path,
        audio_format,
        "?" if duration is None else f"{duration:.1f}s",
        max_seconds,
        normalize,
        sorted(tags) if tags else [],
    )
    completed = subprocess.run(command, capture_output=True)
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"ffmpeg failed for {source_path} (exit {completed.returncode}): {stderr[-800:]}")
    return dest_path


def download_mp3(rttid: str, dest: Path, *, timeout: float = 60.0) -> Path:
    """Download a track's MP3 via the two-step /rest + /bank resolution."""
    logger.info("downloader: downloading MP3 for rttid=%s", rttid)
    info = resolve_track(rttid, timeout=timeout)
    url = track_url(info)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(request, timeout=timeout) as response, open(dest, "wb") as out:
        shutil.copyfileobj(response, out)
    logger.info("downloader: saved MP3 to %s", dest)
    return dest


def download_and_transcode(
    rttid: str,
    dest_path: Path | str,
    *,
    audio_format: str = "mp3",
    ffmpeg: str | None = None,
    ffprobe: str | None = None,
    timeout: float = 60.0,
    max_seconds: float | None = DEFAULT_MAX_SECONDS,
    normalize: bool = True,
    tags: Mapping[str, str] | None = None,
    cache_dir: Path | str | None = None,
) -> Path:
    """Export a track to ``dest_path``, reusing its cached source MP3.

    ``audio_format`` picks the container/encoder and ``tags`` are written into
    the result (title/artist/album) so ES can display them. The source MP3 is
    kept in the cache after transcoding so preview playback and later exports
    of the same track do not download it again. A missing ``cache_dir`` falls
    back to :func:`default_cache_dir`.
    """
    dest_path = Path(dest_path)
    cache_dir = cache_dir or default_cache_dir()
    logger.info(
        "downloader: exporting rttid=%s -> %s (format=%s)", rttid, dest_path, audio_format
    )
    try:
        mp3_path = ensure_cached_mp3(rttid, cache_dir, timeout=timeout)
        transcode_audio(
            mp3_path,
            dest_path,
            audio_format=audio_format,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            max_seconds=max_seconds,
            normalize=normalize,
            tags=tags,
        )
    except Exception:
        logger.exception("downloader: failed to export rttid=%s", rttid)
        raise
    return dest_path
