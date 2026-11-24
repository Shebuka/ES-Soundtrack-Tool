"""Retrotracks (https://retro.sx) scraper.

Parsing is separated from HTTP so the parsers can be tested against the
captured pages in ``tests/fixtures/``. Platform tags on the site are
deliberately ignored; platform resolution happens via ScreenScraper later.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .cache import PageCache, cache_key

BASE_URL = "https://retro.sx"
USER_AGENT = "es-soundtrack-tool/0.1 (+personal use)"

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ParsedSoundtrack:
    sid: str
    title: str
    boxhash: str
    url: str


@dataclass(frozen=True, slots=True)
class ParsedTrack:
    rttid: str
    title: str
    duration: str | None = None
    size_mb: float | None = None
    composer: str | None = None


def parse_size_mb(text: str | None) -> float | None:
    """Parse a Retrotracks size string such as ``"3.11MB"``."""
    if not text:
        return None
    match = re.match(r"([0-9.]+)\s*MB", text.strip(), flags=re.IGNORECASE)
    return float(match.group(1)) if match else None


def _sid_from_href(href: str) -> str:
    match = re.search(r"/music/(\d+)", href)
    return match.group(1) if match else ""


class _IndexParser(HTMLParser):
    """Extract soundtrack rows from the /music index page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.soundtracks: list[ParsedSoundtrack] = []
        self._open = False
        self._buf: list[str] = []
        self._href = ""
        self._boxhash = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if "showGameBox" in classes:
            self._open = True
            self._buf = []
            self._href = a.get("href") or ""
            self._boxhash = a.get("data-boxhash") or ""

    def handle_data(self, data: str) -> None:
        if self._open:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._open:
            title = "".join(self._buf).strip()
            self.soundtracks.append(
                ParsedSoundtrack(
                    sid=_sid_from_href(self._href),
                    title=title,
                    boxhash=self._boxhash,
                    url=self._href,
                )
            )
            self._open = False


class _TrackParser(HTMLParser):
    """Extract track rows from a /music/{id} soundtrack page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tracks: list[ParsedTrack] = []
        self._rttid: str | None = None
        self._title = ""
        self._duration = ""
        self._size: float | None = None
        self._composer = ""
        self._in_name = False
        self._in_duration = False
        self._in_composers = False

    def _reset(self) -> None:
        self._title = ""
        self._duration = ""
        self._size = None
        self._composer = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "tr":
            rttid = a.get("data-rttid")
            if rttid:
                self._reset()
                self._rttid = rttid
        elif tag == "td":
            classes = (a.get("class") or "").split()
            if "ttDuration" in classes:
                self._in_duration = True
                self._size = parse_size_mb(a.get("title"))
        elif tag == "div":
            cls = a.get("class")
            if cls == "ttName":
                self._in_name = True
            elif cls == "ttComposers":
                self._in_composers = True

    def handle_data(self, data: str) -> None:
        if self._rttid is None:
            return
        if self._in_name:
            self._title += data
        elif self._in_duration:
            self._duration += data
        elif self._in_composers:
            self._composer += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "tr" and self._rttid is not None:
            self.tracks.append(
                ParsedTrack(
                    rttid=self._rttid,
                    title=self._title.strip(),
                    duration=self._duration.strip() or None,
                    size_mb=self._size,
                    composer=self._composer.strip() or None,
                )
            )
            self._rttid = None
        elif tag == "td":
            self._in_duration = False
        elif tag == "div":
            self._in_name = False
            self._in_composers = False


def parse_index(html_text: str) -> list[ParsedSoundtrack]:
    parser = _IndexParser()
    parser.feed(html_text)
    return parser.soundtracks


def parse_tracks(html_text: str) -> list[ParsedTrack]:
    parser = _TrackParser()
    parser.feed(html_text)
    return parser.tracks


def resolve_track(rttid: str, *, timeout: float = 30.0) -> dict[str, Any]:
    """Resolve the MP3 hash for a track via the two-step /rest API.

    Deliberately *not* cached: track hashes can rotate upstream, and this runs
    once per selected track at export time, so a stale hash is worse than the
    extra request.
    """
    body = urllib.parse.urlencode({"type": "trackinfo", "id": rttid})
    request = urllib.request.Request(
        f"{BASE_URL}/rest",
        data=body.encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def track_url(track_info: dict[str, Any]) -> str:
    """Build the MP3 URL from a resolve_track result."""
    return f"{BASE_URL}/bank/{track_info['track_hbit']}/{track_info['track_hash']}.mp3"


class RetrotracksClient:
    """Polite HTTP client for the Retrotracks pages.

    Soundtrack page bodies are cached on disk when ``cache_dir`` is set (plan
    §6.5): each one is re-read on every build, and the upstream HTML changes
    rarely, so caching saves hundreds of requests per run.

    The ``/music`` index is deliberately *not* cached — it is a single request
    per run, and a cached copy would hide newly added soundtracks until the whole
    cache directory was wiped.
    """

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        min_delay: float = 1.0,
        cache_dir: Path | str | None = None,
    ) -> None:
        self._timeout = timeout
        self._min_delay = min_delay
        self._cache = PageCache(cache_dir)
        self._last_request = 0.0

    def _fetch(self, url: str, *, use_cache: bool = True) -> str:
        name = cache_key(url, "html")
        if use_cache:
            cached = self._cache.get(name)
            if cached is not None:
                logger.debug("retrotracks: cache hit for %s", url)
                return cached

        wait = self._min_delay - (time.monotonic() - self._last_request)
        if wait > 0:
            logger.debug("retrotracks: rate-limit pause %.2fs", wait)
            time.sleep(wait)
        logger.debug("retrotracks: GET %s", url)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        self._last_request = time.monotonic()
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
        if use_cache:
            self._cache.put(name, body)
        return body

    def fetch_index(self) -> str:
        """Fetch the soundtrack index live, so new soundtracks are always seen."""
        return self._fetch(f"{BASE_URL}/music", use_cache=False)

    def fetch_soundtrack(self, sid: str) -> str:
        return self._fetch(f"{BASE_URL}/music/{sid}")

    def index_soundtracks(self) -> list[ParsedSoundtrack]:
        soundtracks = parse_index(self.fetch_index())
        logger.info("retrotracks: parsed %d soundtracks from index", len(soundtracks))
        return soundtracks

    def soundtrack_tracks(self, sid: str) -> list[ParsedTrack]:
        tracks = parse_tracks(self.fetch_soundtrack(sid))
        logger.info("retrotracks: soundtrack %s has %d tracks", sid, len(tracks))
        return tracks
