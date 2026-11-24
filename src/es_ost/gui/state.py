"""Shared GUI application state: the current project and frozen dataset."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from es_ost.core.dataset import Dataset, load_dataset
from es_ost.core.models import Project, ThemeSystem
from es_ost.core.systemmap import bundled_systems_json, load_theme_systems

logger = logging.getLogger(__name__)


class AppState:
    """Holds the open project and the loaded dataset for the GUI."""

    def __init__(self) -> None:
        self.project = Project()
        self.dataset: Dataset | None = None
        self.current_path: Path | None = None

    def load_dataset(self, data_dir: Path | str) -> Dataset:
        """Load the frozen dataset, falling back to the raw system map."""
        data_dir = Path(data_dir)
        try:
            self.dataset = load_dataset(data_dir)
        except FileNotFoundError:
            logger.warning("state: dataset not found at %s — falling back to raw system map", data_dir)
            self.dataset = Dataset(
                version="unbuilt",
                build_date="",
                theme_systems=load_theme_systems(bundled_systems_json()),
            )
        logger.info(
            "state: loaded dataset version=%s (%d soundtracks)",
            self.dataset.version,
            len(self.dataset.soundtracks),
        )
        return self.dataset

    def theme_systems(self) -> list:
        if self.dataset is None:
            return []
        return self.dataset.theme_systems

    def default_systems(self) -> list[ThemeSystem]:
        """Fresh copies of the dataset's default (ArkOS/RetroArch) systems.

        Copies matter: the GUI mutates ``favourite`` and the project list, and
        the dataset objects must stay untouched.
        """
        if self.dataset is None:
            return []
        return [replace(system) for system in self.dataset.theme_systems if system.in_defaults]
