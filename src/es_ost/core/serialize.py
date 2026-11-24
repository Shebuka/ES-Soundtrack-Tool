"""Dataclass <-> plain-dict conversion.

Shared by the frozen dataset persistence and the project JSON schema so both
formats stay consistent. Models themselves stay free of serialization code.
"""

from __future__ import annotations

from typing import Any

from .models import (
    Assignment,
    Game,
    Platform,
    Provenance,
    RankStatus,
    Soundtrack,
    ThemeKind,
    ThemeSystem,
    Track,
)


def _optional_int(value: Any) -> int | None:
    """Coerce an optional numeric field; ``None``, ``""`` and garbage become ``None``.

    ScreenScraper sends ids as strings in some payloads, and the dataset stores
    whatever the prep layer produced, so every model field typed ``int | None``
    is normalized here rather than at each use site. A malformed value degrades
    the field instead of failing the whole dataset load.
    """
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def platform_to_dict(platform: Platform) -> dict[str, Any]:
    return {
        "screenscraper_id": platform.screenscraper_id,
        "display_name": platform.display_name,
    }


def platform_from_dict(data: dict[str, Any]) -> Platform:
    return Platform(
        screenscraper_id=int(data["screenscraper_id"]),
        display_name=str(data["display_name"]),
    )


def theme_system_to_dict(system: ThemeSystem) -> dict[str, Any]:
    return {
        "theme_name": system.theme_name,
        "rom_folder": system.rom_folder,
        "screenscraper_id": system.screenscraper_id,
        "kind": system.kind.value,
        "in_defaults": system.in_defaults,
        "favourite": system.favourite,
    }


def theme_system_from_dict(data: dict[str, Any]) -> ThemeSystem:
    return ThemeSystem(
        theme_name=str(data["theme_name"]),
        rom_folder=str(data["rom_folder"]),
        screenscraper_id=data.get("screenscraper_id"),
        kind=ThemeKind(data.get("kind", ThemeKind.STANDARD.value)),
        in_defaults=bool(data.get("in_defaults", False)),
        favourite=bool(data.get("favourite", False)),
    )


def game_to_dict(game: Game) -> dict[str, Any]:
    return {
        "id": game.id,
        "name": game.name,
        "screenscraper_ids": game.screenscraper_ids,
        "note": game.note,
        "scrape_count": game.scrape_count,
    }


def game_from_dict(data: dict[str, Any]) -> Game:
    return Game(
        id=int(data["id"]),
        name=str(data["name"]),
        screenscraper_ids=[int(x) for x in data.get("screenscraper_ids", [])],
        note=data.get("note"),
        scrape_count=int(data.get("scrape_count", 0)),
    )


def track_to_dict(track: Track) -> dict[str, Any]:
    return {
        "rttid": track.rttid,
        "title": track.title,
        "duration": track.duration,
        "size_mb": track.size_mb,
        "composer": track.composer,
        "theme_score": track.theme_score,
        "llm_score": track.llm_score,
        "rank_status": track.rank_status.value,
    }


def track_from_dict(data: dict[str, Any]) -> Track:
    return Track(
        rttid=str(data["rttid"]),
        title=str(data["title"]),
        duration=data.get("duration"),
        size_mb=data.get("size_mb"),
        composer=data.get("composer"),
        theme_score=float(data.get("theme_score", 0.0)),
        llm_score=data.get("llm_score"),
        rank_status=RankStatus(data.get("rank_status", RankStatus.KEYWORD.value)),
    )


def soundtrack_to_dict(soundtrack: Soundtrack) -> dict[str, Any]:
    return {
        "url": soundtrack.url,
        "title": soundtrack.title,
        "matched_game_id": soundtrack.matched_game_id,
        "match_confidence": soundtrack.match_confidence,
        "eligible_screenscraper_ids": soundtrack.eligible_screenscraper_ids,
        "tracks": [track_to_dict(track) for track in soundtrack.tracks],
    }


def soundtrack_from_dict(data: dict[str, Any]) -> Soundtrack:
    return Soundtrack(
        url=str(data["url"]),
        title=str(data["title"]),
        matched_game_id=_optional_int(data.get("matched_game_id")),
        match_confidence=data.get("match_confidence"),
        eligible_screenscraper_ids=[int(x) for x in data.get("eligible_screenscraper_ids", [])],
        tracks=[track_from_dict(track) for track in data.get("tracks", [])],
    )


def assignment_to_dict(assignment: Assignment) -> dict[str, Any]:
    return {
        "theme_name": assignment.theme_name,
        "soundtrack_url": assignment.soundtrack_url,
        "track_rttid": assignment.track_rttid,
        "provenance": assignment.provenance.value,
        "locked": assignment.locked,
    }


def assignment_from_dict(data: dict[str, Any]) -> Assignment:
    return Assignment(
        theme_name=str(data["theme_name"]),
        soundtrack_url=str(data["soundtrack_url"]),
        track_rttid=str(data["track_rttid"]),
        provenance=Provenance(data.get("provenance", Provenance.AUTO.value)),
        locked=bool(data.get("locked", False)),
    )
