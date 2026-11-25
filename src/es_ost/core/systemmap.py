"""System identity mapping.

Resolves rom-folder names to ScreenScraper system ids using the bundled
``data/systems.json`` (from emulationstation_gamemanager) and applies the
fallback/classification table documented in ``AGENTS.md``.

This module is used at dataset-prep time to produce the resolved
``theme_systems.json``. The shipped app reads that resolved file instead.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .. import paths
from .models import ThemeKind, ThemeSystem

logger = logging.getLogger(__name__)

# Provisional default subset: the rom folders a freshly formatted ArkOS /
# RetroArch easyroms partition ships with. TODO finalize against the target
# device's actual easyroms partition listing.
DEFAULT_ROM_FOLDERS = frozenset(
    {
        # Tier 1: The Essential Heavyweights
        "snes",
        "sfc",
        "psx",
        "arcade",
        "genesis",
        "megadrive",
        "gba",
        "nes",
        "famicom",
        "n64",
        "psp",
        "nds",
        "dreamcast",
        "neogeo",
        "saturn",
        "gb",
        "gbc",
        "fbneo",
        # Tier 2: Major Consoles & Arcade Boards
        "pcengine",
        "turbografx",
        "mastersystem",
        "amiga",
        "c64",
        "dos",
        "scummvm",
        "naomi",
        "cps2",
        "cps3",
        "cps1",
        "segacd",
        "pcenginecd",
        "turbografxcd",
        "gamegear",
        "atari2600",
        "zxspectrum",
        # Tier 3: Cult Classics, Handhelds & Specialty Systems
        "atomiswave",
        "msx2",
        "x68000",
        "pc98",
        "3do",
        "sega32x",
        "ngpc",
        "wonderswancolor",
        "atarilynx",
        "coleco",
        "intellivision",
        "atari7800",
        # Tier 4: Modern Fantasy Consoles, Ports & Niche Gems
        "ports",
        "pico-8",
        "openbor",
    }
)

# Special theme/rom-folder keys: canonical parent, kind, and a fallback id used
# when systems.json maps the key to null/"". (ggh is null in systems.json.)
_OVERRIDES: dict[str, tuple[str, ThemeKind, int | None]] = {
    "snesh": ("snes", ThemeKind.HACK, None),
    "nesh": ("nes", ThemeKind.HACK, None),
    "genh": ("genesis", ThemeKind.HACK, None),
    "gbh": ("gb", ThemeKind.HACK, None),
    "gbch": ("gbc", ThemeKind.HACK, None),
    "ggh": ("gamegear", ThemeKind.HACK, None),
    "snes-msu1": ("snes", ThemeKind.VARIANT, None),
    "sfc": ("snes", ThemeKind.ALIAS, None),
    "famicom": ("nes", ThemeKind.ALIAS, None),
}


def normalize_screenscraper(value: object) -> int | None:
    """Coerce the heterogeneous ``screenscraper`` field to ``int | None``.

    ``systems.json`` stores this field as an int, a quoted string, ``null``,
    or an empty string. All non-numeric/empty forms mean "unmapped".
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError:
            return None
    return None


def load_raw_mapping(path: Path) -> dict[str, int | None]:
    """Load ``systems.json`` into ``{rom_folder: screenscraper_id | None}``."""
    data = json.loads(path.read_text(encoding="utf-8"))
    mapping = {key: normalize_screenscraper(entry.get("screenscraper")) for key, entry in data.items()}
    logger.info("systemmap: loaded %d rom-folder mappings from %s", len(mapping), path)
    return mapping


def classify_kind(key: str) -> ThemeKind:
    """Return the ThemeKind for a rom-folder/theme key."""
    return _OVERRIDES.get(key, (key, ThemeKind.STANDARD, None))[1]


def alias_targets() -> dict[str, list[str]]:
    """Map canonical folder -> alias theme names (for export alias-copy)."""
    targets: dict[str, list[str]] = {}
    for key, (canonical, kind, _fallback) in _OVERRIDES.items():
        if kind is ThemeKind.ALIAS:
            targets.setdefault(canonical, []).append(key)
    return targets


def build_theme_systems(mapping: dict[str, int | None]) -> list[ThemeSystem]:
    """Build one ThemeSystem per rom-folder key in the mapping."""
    systems: list[ThemeSystem] = []
    for key in sorted(mapping):
        sid = mapping[key]
        _canonical, kind, fallback = _OVERRIDES.get(key, (key, ThemeKind.STANDARD, None))
        if sid is None and fallback is not None:
            sid = fallback
        systems.append(
            ThemeSystem(
                theme_name=key,
                rom_folder=key,
                screenscraper_id=sid,
                kind=kind,
                in_defaults=key in DEFAULT_ROM_FOLDERS,
            )
        )
    return systems


def load_theme_systems(path: Path) -> list[ThemeSystem]:
    """Convenience: read systems.json and build ThemeSystem entries."""
    return build_theme_systems(load_raw_mapping(path))


def bundled_systems_json() -> Path:
    """Locate the bundled rom-folder to ScreenScraper id mapping.

    It lives in ``data/`` next to the frozen dataset so the packaging step
    copies it with the same directory include, and a rebuild never touches it
    (``save_dataset`` writes only the five generated files).
    """
    return paths.data_dir() / "systems.json"
