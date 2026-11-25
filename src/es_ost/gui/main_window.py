"""Single-view main window: Systems group, scrollable system table, Controls group.

The previous three-tab layout (Systems / Manual / Automatic) is replaced by one
vertical view so every system's soundtrack choice is visible and editable in
place. File/Data menus remain for project and dataset housekeeping.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from es_ost import downloader, paths
from es_ost.core import assign
from es_ost.core import output as output_module
from es_ost.core.models import Project
from es_ost.core.project import load_project, save_project

from .export_dialog import ExportProgressDialog, ExportThread, OutputSettingsDialog
from .refresh import RefreshDialog
from .state import AppState
from .systems_view import SystemsTableView
from .wizard import NewProjectDialog

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Top-level window owning the shared AppState and the single-view layout."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ES Soundtrack Tool")
        self.resize(1120, 720)

        self._state = AppState()
        self._state.load_dataset(paths.data_dir())
        # Launch with the default ArkOS/RetroArch systems already in the project.
        self._state.project.systems = self._state.default_systems()

        # One shared audio cache for preview playback and export.
        self._cache_dir = downloader.default_cache_dir()
        self._table = SystemsTableView(self._state, cache_dir=self._cache_dir)
        self._table.playerError.connect(self._on_player_error)
        self._export_thread: ExportThread | None = None

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.setSpacing(16)
        top_bar.addWidget(self._build_project_group())
        top_bar.addWidget(self._build_systems_group())
        top_bar.addStretch(1)
        root.addLayout(top_bar)
        root.addLayout(self._build_table_region(), 1)
        root.addWidget(self._build_controls_group())
        self.setCentralWidget(central)

        self._build_menus()
        self._update_window_title()
        self.statusBar().showMessage("Ready")

    # ------------------------------------------------------------------ layout
    def _action_group(
        self,
        title: str,
        buttons: list[QPushButton],
        *,
        stretch_after: int | None = None,
    ) -> QWidget:
        """A VStack with a label and an HStack of buttons.

        ``stretch_after`` inserts a flexible stretch after that button index,
        which splits the row into left- and right-aligned controls. The default
        appends a trailing stretch so the buttons stay left-aligned.
        """
        group = QWidget()
        stack = QVBoxLayout(group)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(4)

        label = QLabel(title)
        label_font = label.font()
        label_font.setBold(True)
        label.setFont(label_font)
        stack.addWidget(label)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        for index, button in enumerate(buttons):
            row.addWidget(button)
            if stretch_after is not None and index == stretch_after:
                row.addStretch(1)
        if stretch_after is None:
            row.addStretch(1)
        stack.addLayout(row)
        return group

    def _build_project_group(self) -> QWidget:
        new_project = QPushButton("New project…")
        new_project.clicked.connect(self._new_project)
        open_project = QPushButton("Open project…")
        open_project.clicked.connect(self._open_project)
        save_project = QPushButton("Save project")
        save_project.clicked.connect(self._save_project)
        save_as = QPushButton("Save as…")
        save_as.clicked.connect(self._save_project_as)
        return self._action_group(
            "Project", [new_project, open_project, save_project, save_as]
        )

    def _build_systems_group(self) -> QWidget:
        remove_all = QPushButton("Remove all systems")
        remove_all.clicked.connect(self._table.remove_all)
        reset_defaults = QPushButton("Reset to default systems")
        reset_defaults.clicked.connect(self._table.reset_to_defaults)
        add_system = QPushButton("Add system…")
        add_system.clicked.connect(self._table.add_system)
        return self._action_group(
            "Systems", [remove_all, reset_defaults, add_system]
        )

    def _build_table_region(self) -> QVBoxLayout:
        """An HStack with a label and the scrollable table for its content."""
        region = QVBoxLayout()
        region.setContentsMargins(0, 0, 0, 0)
        region.setSpacing(8)

        label = QLabel("Systems and soundtracks")
        label_font = label.font()
        label_font.setBold(True)
        label.setFont(label_font)
        label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        label.setMinimumWidth(140)

        region.addWidget(label)
        region.addWidget(self._table, 1)
        return region

    def _build_controls_group(self) -> QWidget:
        auto_assign = QPushButton("Automatically assign soundtracks")
        auto_assign.clicked.connect(self._auto_assign)
        export = QPushButton("Export soundtrack…")
        export.clicked.connect(self._export)
        output_settings = QPushButton("Output settings…")
        output_settings.clicked.connect(self._output_settings)
        return self._action_group(
            "Controls",
            [auto_assign, export, output_settings],
            stretch_after=0,
        )

    # ------------------------------------------------------------------- menus
    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        data_menu = self.menuBar().addMenu("&Data")

        new_action = QAction("&New project", self)
        new_action.triggered.connect(self._new_project)
        open_action = QAction("&Open…", self)
        open_action.triggered.connect(self._open_project)
        save_action = QAction("&Save", self)
        save_action.triggered.connect(self._save_project)
        save_as_action = QAction("Save &as…", self)
        save_as_action.triggered.connect(self._save_project_as)
        export_action = QAction("&Export sounds…", self)
        export_action.triggered.connect(self._export)
        output_settings_action = QAction("Output &settings…", self)
        output_settings_action.triggered.connect(self._output_settings)

        file_menu.addAction(new_action)
        file_menu.addAction(open_action)
        file_menu.addAction(save_action)
        file_menu.addAction(save_as_action)
        file_menu.addSeparator()
        file_menu.addAction(output_settings_action)
        file_menu.addSeparator()
        file_menu.addAction(export_action)

        refresh_action = QAction("&Refresh data…", self)
        refresh_action.triggered.connect(self._refresh)
        data_menu.addAction(refresh_action)

    def _refresh_view(self) -> None:
        self._table.refresh()

    def _update_window_title(self) -> None:
        """Show whether this is a new project or which file is being edited."""
        if self._state.current_path is None:
            self.setWindowTitle("ES Soundtrack Tool — New project")
        else:
            self.setWindowTitle(f"ES Soundtrack Tool — {self._state.current_path}")

    # ---------------------------------------------------------------- projects
    def _new_project(self) -> None:
        dialog = NewProjectDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            logger.info("main: new project cancelled")
            return
        use_defaults = dialog.use_defaults() and self._state.dataset is not None
        logger.info("main: new project (use_defaults=%s)", use_defaults)
        self._state.project = Project()
        if use_defaults:
            self._state.project.systems = self._state.default_systems()
        self._state.current_path = None
        self._refresh_view()
        self._update_window_title()

    def _open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open project", "", "Project files (*.json)")
        if not path:
            logger.info("main: open project cancelled")
            return
        logger.info("main: opening project %s", path)
        self._state.project = load_project(path)
        self._state.current_path = Path(path)
        self._refresh_view()
        self._update_window_title()

    def _save_project(self) -> None:
        if self._state.current_path is None:
            self._save_project_as()
            return
        logger.info("main: saving project to %s", self._state.current_path)
        save_project(self._state.project, self._state.current_path)
        self._update_window_title()

    def _save_project_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save project", "project.json", "Project files (*.json)")
        if not path:
            logger.info("main: save project as cancelled")
            return
        logger.info("main: saving project as %s", path)
        self._state.current_path = Path(path)
        save_project(self._state.project, self._state.current_path)
        self._update_window_title()

    # ------------------------------------------------------------------ actions
    def _auto_assign(self) -> None:
        dataset = self._state.dataset
        if dataset is None:
            logger.warning("main: automatic assignment requested but no dataset loaded")
            return

        present = {system.theme_name for system in self._state.project.systems}
        locked = [
            assignment
            for assignment in self._state.project.assignments
            if assignment.locked and assignment.theme_name in present
        ]
        locked_names = {assignment.theme_name for assignment in locked}

        logger.info(
            "main: automatic assignment over %d systems (%d locked)",
            len(self._state.project.systems) - len(locked_names),
            len(locked_names),
        )
        generated = assign.auto_assign(
            self._state.project.systems,
            dataset.soundtracks,
            dataset.games_by_platform,
            locked_theme_names=locked_names,
            reserved_soundtrack_urls=[
                assignment.soundtrack_url for assignment in locked
            ],
        )
        self._state.project.assignments = locked + generated
        self._table.refresh_assignments()
        self.statusBar().showMessage(
            f"Automatic assignment: {len(generated)} systems assigned, "
            f"{len(locked)} kept locked"
        )
        logger.info(
            "main: automatic assignment produced %d assignments, kept %d locked",
            len(generated),
            len(locked),
        )

    def _output_settings(self) -> None:
        """Edit the project's export format and preview length."""
        dialog = OutputSettingsDialog(self._state.project, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            logger.info("main: output settings cancelled")
            return
        audio_format = dialog.selected_format()
        seconds = dialog.max_track_seconds()
        self._state.project.output_settings["audio_format"] = audio_format.value
        self._state.project.output_settings["max_track_seconds"] = seconds
        logger.info(
            "main: output settings set to format=%s, max_track_seconds=%s",
            audio_format.value,
            seconds,
        )

    def _export(self) -> None:
        if self._state.dataset is None:
            logger.warning("main: export requested but no dataset loaded")
            return
        directory = QFileDialog.getExistingDirectory(self, "Choose output folder")
        if not directory:
            logger.info("main: export cancelled")
            return

        logger.info("main: exporting to %s", directory)
        dialog = ExportProgressDialog(self)
        thread = ExportThread(
            self._state.project,
            self._state.dataset,
            directory,
            self._cache_dir,
            self,
        )
        written_count: int | None = None
        error_message: str | None = None

        def on_succeeded(count: int) -> None:
            nonlocal written_count
            written_count = count
            dialog.setValue(dialog.maximum())
            dialog.setLabelText(f"Exported {count} files.")
            dialog.finish()

        def on_failed(message: str) -> None:
            nonlocal error_message
            error_message = message
            dialog.finish()

        thread.progress.connect(dialog.update_progress)
        thread.succeeded.connect(on_succeeded)
        thread.failed.connect(on_failed)
        self._export_thread = thread
        QTimer.singleShot(0, thread.start)
        try:
            dialog.exec()
        finally:
            thread.wait()
            thread.deleteLater()
            self._export_thread = None

        if error_message is not None:
            logger.error("main: export failed: %s", error_message)
            QMessageBox.critical(self, "Export failed", error_message)
            return
        count = written_count or 0
        export_format = output_module.resolve_audio_format(self._state.project)
        logger.info("main: export wrote %d %s files", count, export_format.value)
        QMessageBox.information(
            self,
            "Export",
            f"Wrote {count} {export_format.extension.upper()} files to {directory}",
        )

    def _refresh(self) -> None:
        logger.info("main: opening refresh data dialog")
        RefreshDialog(self).exec()

    def _on_player_error(self, message: str) -> None:
        logger.warning("main: player error: %s", message)
        self.statusBar().showMessage(message, 5000)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._table.shutdown()
        super().closeEvent(event)
