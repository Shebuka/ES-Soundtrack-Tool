"""Frozen dataset persistence.

The embedded dataset is written by ``es-ost-prep`` and read by the GUI. The
version manifest enables forward migration as the schema evolves.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import serialize
from .models import Game, Soundtrack, ThemeSystem

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

VERSION_FILE = "version.json"
PLATFORMS_FILE = "platforms.json"
THEME_SYSTEMS_FILE = "theme_systems.json"
SOUNDTRACKS_FILE = "soundtracks.json"
GAMES_FILE = "games_by_platform.json"


@dataclass(slots=True)
class Dataset:
    version: str
    build_date: str
    platforms: dict[int, str] = field(default_factory=dict)
    theme_systems: list[ThemeSystem] = field(default_factory=list)
    soundtracks: list[Soundtrack] = field(default_factory=list)
    games_by_platform: dict[int, list[Game]] = field(default_factory=dict)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_dataset(dataset: Dataset, data_dir: Path | str) -> None:
    """Write the frozen dataset files into ``data_dir``."""
    data_dir = Path(data_dir)
    logger.info(
        "dataset: saving %d soundtracks, %d theme systems, %d platforms to %s",
        len(dataset.soundtracks),
        len(dataset.theme_systems),
        len(dataset.platforms),
        data_dir,
    )
    _write_json(
        data_dir / VERSION_FILE,
        {
            "version": dataset.version,
            "build_date": dataset.build_date,
            "schema_version": SCHEMA_VERSION,
        },
    )
    _write_json(data_dir / PLATFORMS_FILE, {str(k): v for k, v in dataset.platforms.items()})
    _write_json(
        data_dir / THEME_SYSTEMS_FILE,
        [serialize.theme_system_to_dict(system) for system in dataset.theme_systems],
    )
    _write_json(
        data_dir / SOUNDTRACKS_FILE,
        [serialize.soundtrack_to_dict(soundtrack) for soundtrack in dataset.soundtracks],
    )
    _write_json(
        data_dir / GAMES_FILE,
        {
            str(screenscraper_id): [serialize.game_to_dict(game) for game in games]
            for screenscraper_id, games in dataset.games_by_platform.items()
        },
    )


def load_dataset(data_dir: Path | str) -> Dataset:
    """Load the frozen dataset from ``data_dir``."""
    data_dir = Path(data_dir)
    logger.info("dataset: loading dataset from %s", data_dir)
    version = _read_json(data_dir / VERSION_FILE)
    platforms = {
        int(k): v for k, v in _read_json(data_dir / PLATFORMS_FILE).items()
    }
    theme_systems = [
        serialize.theme_system_from_dict(item)
        for item in _read_json(data_dir / THEME_SYSTEMS_FILE)
    ]
    soundtracks = [
        serialize.soundtrack_from_dict(item)
        for item in _read_json(data_dir / SOUNDTRACKS_FILE)
    ]
    games_by_platform = {
        int(k): [serialize.game_from_dict(game) for game in games]
        for k, games in _read_json(data_dir / GAMES_FILE).items()
    }
    return Dataset(
        version=str(version.get("version", "")),
        build_date=str(version.get("build_date", "")),
        platforms=platforms,
        theme_systems=theme_systems,
        soundtracks=soundtracks,
        games_by_platform=games_by_platform,
    )
