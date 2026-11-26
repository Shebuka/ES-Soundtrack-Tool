"""ScreenScraper Web API v2 client.

Handles authentication params, quota-aware rate limiting, disk caching, retry
with exponential backoff, and the ScreenScraper HTTP error-code semantics
documented in ``AGENTS.md``.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from es_ost.core.ranking import standard_form
from es_ost.tls import ssl_context

from .cache import PageCache, cache_key

API_BASE_URL = "https://api.screenscraper.fr/api2/"
USER_AGENT = "es-soundtrack-tool/0.1 (+personal use)"

logger = logging.getLogger(__name__)


class ScreenScraperError(Exception):
    """Base class for ScreenScraper API errors.

    ``fatal`` tells batch callers whether continuing makes sense. Session-level
    failures (bad credentials, blacklisting, closed API, exhausted quota) fail
    every later request identically, so a batch must abort instead of emitting
    thousands of degraded items. Per-request failures (404, rate limit,
    unusable body after retries) only affect the item being fetched, so a batch
    logs them and carries on.
    """

    fatal: bool = False

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class AuthError(ScreenScraperError):
    """403: bad developer credentials, or login verification failed."""

    fatal = True


class BlacklistedError(ScreenScraperError):
    """426: the calling softname has been blacklisted."""

    fatal = True


class RateLimitedError(ScreenScraperError):
    """429: thread/rate limit reached."""


class QuotaExceededError(ScreenScraperError):
    """430: daily scrape quota exceeded."""

    fatal = True


class UnrecognizedLimitError(ScreenScraperError):
    """431: too many unrecognized roms today."""

    fatal = True


class ApiClosedError(ScreenScraperError):
    """401/423: the API is closed."""

    fatal = True


class NotFoundError(ScreenScraperError):
    """404: game/rom not found."""


class InvalidResponseError(ScreenScraperError):
    """The body was not JSON, or its header did not report success."""


_ERROR_TYPES: dict[int, type[ScreenScraperError]] = {
    401: ApiClosedError,
    403: AuthError,
    404: NotFoundError,
    423: ApiClosedError,
    426: BlacklistedError,
    429: RateLimitedError,
    430: QuotaExceededError,
    431: UnrecognizedLimitError,
}


_SECRET_PARAM_HINTS = ("password", "ssid", "devid")


def describe_params(params: dict[str, Any]) -> str:
    """Render caller parameters for a log line, never including credentials.

    A bare ``GET jeuInfos.php`` says nothing about which game is being fetched,
    so the identifying parameters are logged — but the base params carry
    ``devid``/``devpassword``/``ssid``/``sspassword`` and those must never reach
    a log file.
    """
    safe = {
        key: value
        for key, value in params.items()
        if not any(hint in key.lower() for hint in _SECRET_PARAM_HINTS)
    }
    if not safe:
        return ""
    return " " + " ".join(f"{key}={value}" for key, value in sorted(safe.items()))


def parse_api_response(body: str) -> dict[str, Any]:
    """Decode and validate a ScreenScraper API body.

    Every endpoint answers with an envelope carrying a status header::

        {"header": {"success": "true", "error": "", ...}, "response": {...}}

    Outages, database errors and quota problems still answer 200 while the
    header reports ``success`` as falsy (and the body is sometimes plain text
    instead of JSON at all). Only a parsed body whose header reports success is
    safe to cache — anything else must stay out of the permanent cache so the
    next run retries the request.

    Args:
        body: Raw response body.

    Returns:
        The decoded envelope.

    Raises:
        InvalidResponseError: If the body is not a JSON object, has no header
            block, or its header reports a failure.
    """
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        snippet = " ".join(body.split())[:200]
        raise InvalidResponseError(f"body is not JSON ({exc}); body={snippet!r}") from exc

    if not isinstance(data, dict):
        raise InvalidResponseError(
            f"body is a JSON {type(data).__name__}, expected an object"
        )

    header = data.get("header")
    if not isinstance(header, dict):
        raise InvalidResponseError("response has no header block")

    success = header.get("success")
    if success is not True and str(success).strip().lower() != "true":
        detail = str(header.get("error") or "").strip() or "no error message given"
        raise InvalidResponseError(f"API reported failure: {detail}")

    return data


#: "The Sims 3: Pets" is filed as "Sims 3, The - Pets" on some platforms, with
#: the article before the subtitle rather than at the end of the whole name.
_ARTICLE_BEFORE_SUBTITLE = re.compile(
    r"^the\s+(?P<series>[^:]+?)\s*:\s*(?P<subtitle>.+)$", re.IGNORECASE
)


def _article_before_subtitle(name: str) -> str | None:
    """``"The Sims 3: Pets"`` -> ``"Sims 3, The - Pets"``; ``None`` without a colon."""
    match = _ARTICLE_BEFORE_SUBTITLE.match(name.strip())
    if match is None:
        return None
    return f"{standard_form(match.group('series'))}, The - {standard_form(match.group('subtitle'))}"


def query_variants(name: str) -> list[str]:
    """Return search-query variants covering ScreenScraper's naming quirks.

    ScreenScraper's standardized (``ss``) names rewrite colons, ampersands,
    plus signs and roman numerals and move a leading "The" to the end
    (:func:`es_ost.core.ranking.standard_form`), and colon spacing is
    inconsistent across the database. Try, in order: the standardized form, that
    form with the article before the dash ("Sims 3, The - Pets" — the same game
    is filed both ways, depending on the platform), the name exactly as the
    soundtrack spells it, then the substring before the colon (from the
    article-before-dash form when there is one, plus the raw name). The original
    name comes before the prefix so a query that only exists under its full
    title is not cut down to a subtitle-free name that matches a different game.
    """
    name = name.strip()
    if not name:
        return []

    form = standard_form(name)
    variants = [form]
    article_mid = None
    if ":" in name:
        article_mid = _article_before_subtitle(name)
        if article_mid and article_mid not in variants:
            variants.append(article_mid)

    if name not in variants:
        variants.append(name)

    if ":" in name:
        if article_mid:
            prefix = article_mid.split("-", 1)[0].strip()
            if prefix not in variants:
                variants.append(prefix)
        else:
            prefix = form.split("-", 1)[0].strip()
            if prefix not in variants:
                variants.append(prefix)
        prefix = name.split(":", 1)[0].strip()
        if prefix not in variants:
            variants.append(prefix)

    return variants


@dataclass(slots=True)
class Quota:
    """The user's ScreenScraper quota, read from ssuserInfos.php."""

    maxrequestspermin: int = 0
    maxrequestsperday: int = 0
    requeststoday: int = 0
    maxthreads: int = 0
    maxdownloadspeed: int = 0

    @classmethod
    def from_ssuser(cls, ssuser: dict[str, Any]) -> "Quota":
        def to_int(value: Any) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0

        return cls(
            maxrequestspermin=to_int(ssuser.get("maxrequestspermin")),
            maxrequestsperday=to_int(ssuser.get("maxrequestsperday")),
            requeststoday=to_int(ssuser.get("requeststoday")),
            maxthreads=to_int(ssuser.get("maxthreads")),
            maxdownloadspeed=to_int(ssuser.get("maxdownloadspeed")),
        )


class ScreenScraperClient:
    """Minimal ScreenScraper API v2 client with caching and rate limiting."""

    def __init__(
        self,
        devid: str,
        devpassword: str,
        softname: str,
        *,
        ssid: str | None = None,
        sspassword: str | None = None,
        timeout: float = 30.0,
        min_delay: float = 1.0,
        max_retries: int = 4,
        cache_dir: Path | str | None = None,
    ) -> None:
        self._devid = devid
        self._devpassword = devpassword
        self._softname = softname
        self._ssid = ssid
        self._sspassword = sspassword
        self._timeout = timeout
        self._min_delay = min_delay
        self._max_retries = max_retries
        self._cache = PageCache(cache_dir)
        self._last_request = 0.0

    def _base_params(self) -> dict[str, str]:
        params = {
            "devid": self._devid,
            "devpassword": self._devpassword,
            "softname": self._softname,
            "output": "json",
        }
        if self._ssid:
            params["ssid"] = self._ssid
        if self._sspassword:
            params["sspassword"] = self._sspassword
        return params

    def _get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        merged = {**self._base_params(), **{k: str(v) for k, v in params.items()}}
        url = API_BASE_URL + endpoint + "?" + urllib.parse.urlencode(merged)
        name = cache_key(url, "json")

        described = describe_params(params)
        cached = self._cache.get(name)
        if cached is not None:
            try:
                data = parse_api_response(cached)
            except InvalidResponseError as exc:
                logger.warning(
                    "screenscraper: ignoring unusable cache entry for %s: %s", endpoint, exc
                )
            else:
                logger.debug("screenscraper: cache hit for %s%s", endpoint, described)
                return data

        for attempt in range(self._max_retries + 1):
            wait = self._min_delay - (time.monotonic() - self._last_request)
            if wait > 0:
                logger.debug("screenscraper: rate-limit pause %.2fs", wait)
                time.sleep(wait)
            logger.debug(
                "screenscraper: GET %s%s (attempt %d)", endpoint, described, attempt + 1
            )
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            self._last_request = time.monotonic()
            try:
                with urllib.request.urlopen(
                    request,
                    timeout=(self._timeout * (attempt + 1)),
                    context=ssl_context(),
                ) as response:
                    body = response.read().decode("utf-8")
            except urllib.error.HTTPError as exc:
                mapped = self._map_error(exc)
                if isinstance(mapped, RateLimitedError) and attempt < self._max_retries:
                    logger.warning(
                        "screenscraper: rate limited on %s — retrying (attempt %d)",
                        endpoint,
                        attempt + 1,
                    )
                    time.sleep(2 ** attempt)
                    continue
                logger.error("screenscraper: HTTP %s on %s", exc.code, endpoint)
                raise mapped
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt < self._max_retries:
                    logger.warning(
                        "screenscraper: network error on %s — retrying (attempt %d): %s",
                        endpoint,
                        attempt + 1,
                        exc,
                    )
                    time.sleep(2 ** attempt)
                    continue
                logger.error("screenscraper: network error on %s: %s", endpoint, exc)
                raise ScreenScraperError(f"network error: {exc}") from exc

            try:
                data = parse_api_response(body)
            except InvalidResponseError as exc:
                if attempt < self._max_retries:
                    logger.warning(
                        "screenscraper: unusable response on %s — retrying (attempt %d): %s",
                        endpoint,
                        attempt + 1,
                        exc,
                    )
                    time.sleep(2 ** attempt)
                    continue
                logger.error("screenscraper: unusable response on %s: %s", endpoint, exc)
                raise

            self._cache.put(name, body)
            return data

        raise ScreenScraperError("unreachable")

    @staticmethod
    def _map_error(exc: urllib.error.HTTPError) -> ScreenScraperError:
        error_type = _ERROR_TYPES.get(exc.code, ScreenScraperError)
        return error_type(f"ScreenScraper HTTP {exc.code}", exc.code)

    @property
    def min_delay(self) -> float:
        """Pause enforced between requests, in seconds."""
        return self._min_delay

    def apply_quota(self, quota: Quota) -> None:
        """Throttle to the rate the user's account is actually allowed.

        ScreenScraper enforces ``maxrequestspermin`` per account, and the value
        differs by two orders of magnitude between an anonymous and a registered
        user. Spacing every request by ``60 / maxrequestspermin`` keeps the
        client at the edge of the allowance instead of guessing: a 1/min account
        waits a minute (and so never trips the 429 retry path), a 600/min
        account is no longer held back by the conservative default.

        Overrides ``min_delay`` on purpose — the constructor value is only the
        throttle used until the quota is known. A quota without a per-minute
        limit (``0``) leaves it untouched.
        """
        if quota.maxrequestspermin <= 0:
            logger.debug(
                "screenscraper: quota reports no per-minute limit — keeping %.2fs throttle",
                self._min_delay,
            )
            return
        self._min_delay = 60.0 / quota.maxrequestspermin
        logger.info(
            "screenscraper: throttling to %.2fs between requests (%d req/min)",
            self._min_delay,
            quota.maxrequestspermin,
        )

    def verify_login(self) -> Quota:
        """Verify credentials, read the quota and throttle to it."""
        data = self._get("ssuserInfos.php", {})
        ssuser = data.get("response", {}).get("ssuser", {})
        if not ssuser:
            raise AuthError("login verification failed: no ssuser in response")
        quota = Quota.from_ssuser(ssuser)
        logger.info("screenscraper: login verified — %s", quota)
        self.apply_quota(quota)
        return quota

    def search(self, name: str, systemeid: int | None = None) -> list[dict[str, Any]]:
        """Search by name; returns up to 30 candidates sorted by probability.

        A no-result response is ``[{}]`` (a single empty object), which is
        normalized to ``[]`` here so callers can treat an empty list as "no
        match".
        """
        params: dict[str, Any] = {"recherche": name}
        if systemeid is not None:
            params["systemeid"] = systemeid
        data = self._get("jeuRecherche.php", params)
        response = data.get("response", {})
        candidates = response.get("jeux") or response.get("jeu") or []
        if isinstance(candidates, dict):
            candidates = [candidates]
        result = [candidate for candidate in candidates if candidate]
        logger.debug("screenscraper: search %r -> %d candidates", name, len(result))
        return result

    def search_variants(self, name: str, systemeid: int | None = None) -> list[dict[str, Any]]:
        """Search trying colon-safe variants until one returns candidates.

        ``jeuRecherche`` returns an empty ``jeu`` list (never a 404) when
        nothing matches, so an empty list simply triggers the next variant.

        A transient failure on one variant is logged and skipped so the
        remaining variants still get a chance. If none matched, the last
        transient error is re-raised — callers must be able to tell "the server
        failed" from "no such game", otherwise a degraded item would look like
        a legitimate no-match. Fatal errors abort immediately.
        """
        transient_error: ScreenScraperError | None = None
        for variant in query_variants(name):
            try:
                candidates = self.search(variant, systemeid=systemeid)
            except ScreenScraperError as exc:
                if exc.fatal:
                    raise
                logger.warning(
                    "screenscraper: variant %r failed — trying the next variant: %s",
                    variant,
                    exc,
                )
                transient_error = exc
                continue
            if candidates:
                logger.debug(
                    "screenscraper: variant %r matched (%d candidates)",
                    variant,
                    len(candidates),
                )
                return candidates
        if transient_error is not None:
            raise transient_error
        logger.debug("screenscraper: no candidates for %r", name)
        return []

    def game_info(self, gameid: int) -> dict[str, Any]:
        """Fetch game metadata by id (no rom hash needed).

        Returns the ``jeu`` object (which includes ``roms`` and ``note``), not
        the surrounding ``response`` envelope.
        """
        data = self._get("jeuInfos.php", {"gameid": gameid})
        response = data.get("response", {})
        return response.get("jeu", response)

    def systems(self) -> list[dict[str, Any]]:
        """List ScreenScraper systems (used to validate the id mapping)."""
        data = self._get("systemesListe.php", {})
        response = data.get("response", {})
        systems = response.get("systemes") or []
        if isinstance(systems, dict):
            systems = [systems]
        return systems

