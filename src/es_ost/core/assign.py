"""Automatic assignment engine."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from . import ranking
from .models import Assignment, Game, Provenance, Soundtrack, ThemeSystem

logger = logging.getLogger(__name__)


def section1_soundtracks(system: ThemeSystem, soundtracks: list[Soundtrack]) -> list[Soundtrack]:
    """Soundtracks whose eligible id set contains the system's ScreenScraper id."""
    if system.screenscraper_id is None:
        return []
    return [
        soundtrack
        for soundtrack in soundtracks
        if system.screenscraper_id in soundtrack.eligible_screenscraper_ids
    ]


def auto_assign(
    systems: list[ThemeSystem],
    soundtracks: list[Soundtrack],
    games_by_platform: dict[int, list[Game]],
    *,
    locked_theme_names: set[str] | None = None,
    reserved_soundtrack_urls: Iterable[str] | None = None,
) -> list[Assignment]:
    """Greedy assignment: favourites first, prefer least-used soundtracks.

    Systems named in ``locked_theme_names`` keep whatever assignment the user
    already made and are skipped entirely. ``reserved_soundtrack_urls`` seeds
    the usage counter with those locked choices, so automatic mode treats them
    as already used and prefers another soundtrack while one is available.
    """
    scrape_by_game = {
        game.id: game.scrape_count
        for games in games_by_platform.values()
        for game in games
    }

    def top_scrape(system: ThemeSystem) -> int:
        games = games_by_platform.get(system.screenscraper_id, [])
        return games[0].scrape_count if games else 0

    locked = locked_theme_names or set()
    eligible = [system for system in systems if system.theme_name not in locked]
    ordered = sorted(eligible, key=lambda system: (-system.favourite, -top_scrape(system)))

    logger.info(
        "assign: auto-assigning %d systems over %d soundtracks (%d locked)",
        len(eligible),
        len(soundtracks),
        len(systems) - len(eligible),
    )

    used_count: dict[str, int] = {}
    for url in reserved_soundtrack_urls or ():
        used_count[url] = used_count.get(url, 0) + 1
    assignments: list[Assignment] = []

    for system in ordered:
        candidates = section1_soundtracks(system, soundtracks)
        logger.info(
            "assign: system %r (favourite=%s) -> %d matching soundtracks",
            system.theme_name,
            system.favourite,
            len(candidates),
        )
        if not candidates:
            continue
        candidates.sort(
            key=lambda soundtrack: (
                used_count.get(soundtrack.url, 0),
                -(scrape_by_game.get(soundtrack.matched_game_id, 0)),
            )
        )
        chosen = candidates[0]
        track = ranking.highest_scored_track(chosen.tracks)
        if track is None:
            logger.info("assign: system %r has no scored track", system.theme_name)
            continue

        logger.info(
            "assign: system %r chose %r (%s) from %d candidates",
            system.theme_name,
            chosen.title,
            chosen.url,
            len(candidates),
        )
        used_count[chosen.url] = used_count.get(chosen.url, 0) + 1
        assignments.append(
            Assignment(
                theme_name=system.theme_name,
                soundtrack_url=chosen.url,
                track_rttid=track.rttid,
                provenance=Provenance.AUTO,
            )
        )

    logger.info("assign: produced %d assignments", len(assignments))
    return assignments
