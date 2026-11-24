"""Export settings dialog, plus the progress dialog and worker thread."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QProgressDialog,
    QVBoxLayout,
    QWidget,
)

from es_ost.core import output as output_module
from es_ost.core.dataset import Dataset
from es_ost.core.models import Project

logger = logging.getLogger(__name__)


class OutputSettingsDialog(QDialog):
    """Edit the project's export format and preview length.

    Both settings are read together by the exporter, so they live in one modal
    — chaining a second input dialog after the length prompt would make the
    format choice easy to miss. ``Cancel`` writes nothing.
    """

    def __init__(self, project: Project, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Output settings")
        self.setModal(True)

        self._format = QComboBox(self)
        for audio_format in output_module.AUDIO_FORMATS:
            self._format.addItem(audio_format.label, audio_format.value)
        current = output_module.resolve_audio_format(project)
        self._format.setCurrentIndex(max(self._format.findData(current.value), 0))

        self._seconds = QDoubleSpinBox(self)
        self._seconds.setRange(0.0, 3600.0)
        self._seconds.setDecimals(0)
        self._seconds.setSingleStep(5.0)
        self._seconds.setSuffix(" s")
        self._seconds.setSpecialValueText("Full track")
        seconds = output_module.max_track_seconds(project)
        self._seconds.setValue(seconds if seconds is not None else 0.0)

        self._hint = QLabel(self)
        self._hint.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Audio format:", self._format)
        form.addRow("Preview length:", self._seconds)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self._hint)
        layout.addWidget(buttons)

        self._format.currentIndexChanged.connect(self._update_hint)
        self._update_hint()

    def selected_format(self) -> output_module.AudioFormat:
        """Format currently picked in the combo box."""
        return output_module.AudioFormat(self._format.currentData())

    def max_track_seconds(self) -> float:
        """Preview length; ``0`` means "full track" (see the exporter)."""
        return float(self._seconds.value())

    def _update_hint(self) -> None:
        audio_format = self.selected_format()
        if audio_format is output_module.AudioFormat.MP3:
            self._hint.setText(
                "MP3 is the only format EmulationStation reliably shows title and "
                "artist for. The theme's bgsound path must end in .mp3."
            )
        else:
            self._hint.setText(
                f"{audio_format.extension.upper()} may show no title/artist in ES, and "
                f"the theme's bgsound path must end in .{audio_format.extension}."
            )


class ExportProgressDialog(QProgressDialog):
    """Deterministic progress dialog for an export that cannot be cancelled.

    The exporter does not support cancellation (a half-written MP3 would be
    worse than waiting), so the dialog hides its cancel control and ignores
    window-close requests until ``finish()`` is called by the worker result.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Preparing export…", "", 0, 0, parent)
        self.setWindowTitle("Export soundtrack")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setCancelButton(None)
        self.setMinimumDuration(0)
        self.setAutoClose(False)
        self.setAutoReset(False)
        self._finished = False

    def update_progress(self, position: int, total: int, theme_name: str) -> None:
        """Apply one ``(position, total, theme_name)`` update from the worker."""
        if total <= 0:
            self.setRange(0, 1)
            self.setValue(0)
            self.setLabelText("Nothing to export.")
            return
        self.setRange(0, total)
        self.setValue(position)
        if theme_name:
            self.setLabelText(f"Exporting {position} of {total}: {theme_name}")
        else:
            self.setLabelText(f"Preparing export of {total} tracks…")

    def finish(self) -> None:
        """Allow the dialog to close and close it."""
        self._finished = True
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._finished:
            super().closeEvent(event)
        else:
            event.ignore()


class ExportThread(QThread):
    """Run the blocking exporter off the UI thread and report progress."""

    progress = Signal(int, int, str)
    succeeded = Signal(int)
    failed = Signal(str)

    def __init__(
        self,
        project: Project,
        dataset: Dataset,
        output_dir: Path | str,
        cache_dir: Path | str | None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._project = project
        self._dataset = dataset
        self._output_dir = output_dir
        self._cache_dir = cache_dir

    def run(self) -> None:
        logger.info("export dialog: starting export to %s", self._output_dir)
        try:
            written = output_module.export(
                self._project,
                self._dataset,
                self._output_dir,
                cache_dir=self._cache_dir,
                progress=self.progress.emit,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced in the GUI
            logger.exception("export dialog: export failed")
            self.failed.emit(str(exc))
            return
        logger.info("export dialog: export wrote %d files", len(written))
        self.succeeded.emit(len(written))
