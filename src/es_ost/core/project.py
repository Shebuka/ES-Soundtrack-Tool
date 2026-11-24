"""Project save/load as versioned JSON."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from . import serialize
from .models import Project

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


def save_project(project: Project, path: Path | str) -> None:
    """Write a project to disk."""
    path = Path(path)
    data: dict[str, Any] = {
        "name": project.name,
        "systems": [serialize.theme_system_to_dict(system) for system in project.systems],
        "favourites": project.favourites,
        "assignments": [serialize.assignment_to_dict(assignment) for assignment in project.assignments],
        "output_settings": project.output_settings,
        "dataset_version": project.dataset_version,
        "schema_version": project.schema_version,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(
        "project: saved %d systems, %d assignments to %s",
        len(project.systems),
        len(project.assignments),
        path,
    )


def load_project(path: Path | str) -> Project:
    """Load a project from disk, rejecting unsupported schema versions."""
    path = Path(path)
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    schema_version = int(data.get("schema_version", SCHEMA_VERSION))
    if schema_version != SCHEMA_VERSION:
        raise ValueError(f"unsupported project schema version {schema_version}")
    logger.info("project: loaded project %r from %s", data.get("name"), path)
    return Project(
        name=str(data.get("name", "New Project")),
        systems=[serialize.theme_system_from_dict(system) for system in data.get("systems", [])],
        favourites=[str(f) for f in data.get("favourites", [])],
        assignments=[serialize.assignment_from_dict(assignment) for assignment in data.get("assignments", [])],
        output_settings=dict(data.get("output_settings", {})),
        dataset_version=data.get("dataset_version"),
        schema_version=schema_version,
    )
