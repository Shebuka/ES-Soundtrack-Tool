"""Domain model dataclasses for the ES Soundtrack Tool.

These are plain, dependency-free data holders. Serialization to/from the
frozen dataset and project JSON lives in :mod:`es_ost.core.dataset` and
:mod:`es_ost.core.project` respectively, not here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ThemeKind(str, Enum):
    """How a theme system relates to its canonical platform."""

    STANDARD = "standard"
    HACK = "hack"
    VARIANT = "variant"
    ALIAS = "alias"


class RankStatus(str, Enum):
    """Where a track sits in the two-phase theme-song ranking pipeline."""

    KEYWORD = "keyword"  # only the provisional keyword score exists
    PENDING = "pending"  # queued for the post-CLI LLM ranking pass
    LLM = "llm"  # authoritative llm_score present


class Provenance(str, Enum):
    """How an assignment was chosen."""

    MANUAL = "manual"
    AUTO = "auto"


@dataclass(frozen=True, slots=True)
class Platform:
    """Canonical identity for ranking, keyed by ScreenScraper system id."""

    screenscraper_id: int
    display_name: str


@dataclass(slots=True)
class ThemeSystem:
    """One output file (``<theme_name>.mp3``)."""

    theme_name: str
    rom_folder: str
    screenscraper_id: int | None = None
    kind: ThemeKind = ThemeKind.STANDARD
    in_defaults: bool = False
    favourite: bool = False


@dataclass(slots=True)
class Game:
    """A game from ScreenScraper."""

    id: int
    name: str
    screenscraper_ids: list[int] = field(default_factory=list)
    note: int | None = None  # community score 0-20 (NOT the PEGI/ESRB rating)
    scrape_count: int = 0


@dataclass(slots=True)
class Track:
    """One song within a Retrotracks soundtrack."""

    rttid: str
    title: str
    duration: str | None = None
    size_mb: float | None = None
    composer: str | None = None
    theme_score: float = 0.0  # provisional keyword score (fallback)
    llm_score: float | None = None  # authoritative, set by the post-CLI pass
    rank_status: RankStatus = RankStatus.KEYWORD


@dataclass(slots=True)
class Soundtrack:
    """A Retrotracks soundtrack matched to a ScreenScraper game."""

    url: str
    title: str
    matched_game_id: int | None = None
    match_confidence: float | None = None
    eligible_screenscraper_ids: list[int] = field(default_factory=list)
    tracks: list[Track] = field(default_factory=list)


@dataclass(slots=True)
class Assignment:
    """The result of manual or automatic mode for one theme system."""

    theme_name: str
    soundtrack_url: str
    track_rttid: str
    provenance: Provenance = Provenance.AUTO
    locked: bool = False
    """When true, automatic assignment leaves this system untouched."""


@dataclass(slots=True)
class Project:
    """A user project: systems, favourites, assignments, output settings."""

    name: str = "New Project"
    systems: list[ThemeSystem] = field(default_factory=list)
    favourites: list[str] = field(default_factory=list)
    assignments: list[Assignment] = field(default_factory=list)
    output_settings: dict = field(default_factory=dict)
    dataset_version: str | None = None
    schema_version: int = 1
