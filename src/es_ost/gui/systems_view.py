"""Scrollable, UITableView-style system table for the single-view GUI.

The window layout itself lives in :mod:`es_ost.gui.main_window`; this module
owns the middle region: a scroll area whose content is a vertical stack of
sections (favourites first, all other systems second), each with a header and
one :class:`SystemCell` per system.
"""

from __future__ import annotations

import logging
from dataclasses import replace

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPalette, QStandardItemModel
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from es_ost.core import assign, ranking
from es_ost.core.models import Assignment, Provenance, Soundtrack, ThemeSystem, Track

from .player import TrackPlayer

logger = logging.getLogger(__name__)

_SOUNDTRACK_ROLE = Qt.ItemDataRole.UserRole


class AddSystemDialog(QDialog):
    """Pick a theme system not yet present in the project."""

    def __init__(self, available: list[ThemeSystem], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add system")
        self._selected: ThemeSystem | None = None

        self._list = QListWidget()
        for system in available:
            item = QListWidgetItem(f"{system.theme_name}  ({system.kind.value})")
            item.setData(Qt.ItemDataRole.UserRole, system)
            self._list.addItem(item)

        self._favourite = QPushButton("☆ Add as favourite")
        self._favourite.setCheckable(True)
        self._favourite.setToolTip("Add this system to the Favourite systems section")
        self._favourite.toggled.connect(self._on_favourite_toggled)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._list)
        layout.addWidget(self._favourite)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        item = self._list.currentItem()
        if item is not None:
            self._selected = item.data(Qt.ItemDataRole.UserRole)
        self.accept()

    def _on_favourite_toggled(self, checked: bool) -> None:
        self._favourite.setText("★ Add as favourite" if checked else "☆ Add as favourite")

    def selected(self) -> ThemeSystem | None:
        return self._selected

    def as_favourite(self) -> bool:
        """Whether the user asked for the new system to start as a favourite."""
        return self._favourite.isChecked()


def _disable_item(combo: QComboBox, index: int) -> None:
    """Make a combo row unselectable (used for the section sentinels)."""
    model = combo.model()
    if not isinstance(model, QStandardItemModel):
        return
    item = model.item(index)
    if item is None:
        return
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
    item.setEnabled(False)


def _add_group_header(combo: QComboBox, text: str) -> None:
    combo.addItem(text)
    _disable_item(combo, combo.count() - 1)


def _add_empty_placeholder(combo: QComboBox, text: str) -> None:
    combo.addItem(text)
    _disable_item(combo, combo.count() - 1)


def _index_for_soundtrack(combo: QComboBox, url: str) -> int:
    for index in range(combo.count()):
        data = combo.itemData(index, _SOUNDTRACK_ROLE)
        if isinstance(data, Soundtrack) and data.url == url:
            return index
    return -1


def _track_label(track: Track) -> str:
    """Track title with the same rating the auto-assignment engine uses."""
    return f"{track.title} — {ranking.effective_score(track):.1f}"


def _make_icon_button(
    text: str,
    tooltip: str,
    accessible_name: str,
    *,
    checkable: bool = False,
) -> QPushButton:
    """Build one of the compact UTF-8 icon buttons used in a system cell."""
    button = QPushButton(text)
    button.setCheckable(checkable)
    button.setFixedSize(34, 30)
    font = button.font()
    font.setPointSize(font.pointSize() + 3)
    button.setFont(font)
    button.setToolTip(tooltip)
    button.setAccessibleName(accessible_name)
    return button


class SystemCell(QFrame):
    """One table row: system label, soundtrack and track pickers, lock, remove."""

    removeRequested = Signal(str)
    favouriteToggled = Signal(str, bool)
    assignmentChanged = Signal()

    def __init__(
        self,
        state,
        system: ThemeSystem,
        player: TrackPlayer,
        striped: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._system = system
        self._player = player
        self._refreshing = False

        self.setObjectName("systemCell")
        self.setMinimumHeight(40)
        self.setAutoFillBackground(True)
        palette = self.palette()
        if striped:
            palette.setColor(QPalette.ColorRole.Window, palette.light().color())
        else:
            palette.setColor(QPalette.ColorRole.Window, palette.mid().color())
        self.setPalette(palette)

        self._favourite = _make_icon_button(
            "★" if system.favourite else "☆",
            "Remove this system from favourites"
            if system.favourite
            else "Add this system to favourites",
            f"{'Unfavourite' if system.favourite else 'Favourite'} {system.theme_name}",
            checkable=True,
        )
        self._favourite.setChecked(system.favourite)
        self._favourite.toggled.connect(self._on_favourite_toggled)

        self._name = QLabel(system.theme_name)
        name_font = self._name.font()
        name_font.setBold(True)
        self._name.setFont(name_font)
        self._name.setMinimumWidth(110)
        self._name.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._name.setToolTip(
            f"{system.theme_name} — {system.kind.value} "
            f"(ScreenScraper id {system.screenscraper_id})"
        )

        self._soundtrack = QComboBox()
        self._soundtrack.setPlaceholderText("Select a soundtrack…")
        self._soundtrack.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._soundtrack.setMinimumContentsLength(20)
        self._soundtrack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._soundtrack.setAccessibleName(f"Soundtrack for {system.theme_name}")
        self._soundtrack.currentIndexChanged.connect(self._on_soundtrack_changed)

        self._track = QComboBox()
        self._track.setPlaceholderText("Select a track…")
        self._track.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._track.setMinimumContentsLength(14)
        self._track.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._track.setAccessibleName(f"Track for {system.theme_name}")
        self._track.currentIndexChanged.connect(self._on_track_changed)

        self._play = _make_icon_button(
            "▶",
            "Preview this track from the cache",
            f"Preview track for {system.theme_name}",
        )
        self._play.setEnabled(False)
        self._play.clicked.connect(self._on_play_clicked)

        self._lock = _make_icon_button(
            "🔓",
            "Lock this system so automatic assignment keeps the current choice",
            f"Lock soundtrack for {system.theme_name}",
            checkable=True,
        )
        self._lock.toggled.connect(self._on_lock_toggled)

        self._remove = _make_icon_button(
            "🗑",
            f"Remove {system.theme_name} from the project",
            f"Remove system {system.theme_name}",
        )
        self._remove.clicked.connect(self._on_remove_clicked)

        row = QHBoxLayout(self)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(8)
        row.addWidget(self._favourite)
        row.addWidget(self._name)
        row.addWidget(self._soundtrack, 3)
        row.addWidget(self._track, 2)
        row.addWidget(self._play)
        row.addWidget(self._lock)
        row.addWidget(self._remove)

        self._player.stateChanged.connect(self._on_player_state_changed)
        self._player.preparing.connect(self._on_player_preparing)
        self._player.errorOccurred.connect(self._on_player_error)

        self.refresh()

    # ------------------------------------------------------------------ state
    def _assignment(self) -> Assignment | None:
        for assignment in self._state.project.assignments:
            if assignment.theme_name == self._system.theme_name:
                return assignment
        return None

    def _soundtracks(self) -> list[Soundtrack]:
        if self._state.dataset is None:
            return []
        return self._state.dataset.soundtracks

    def _soundtrack_for(self, assignment: Assignment | None) -> Soundtrack | None:
        if assignment is None:
            return None
        for soundtrack in self._soundtracks():
            if soundtrack.url == assignment.soundtrack_url:
                return soundtrack
        return None

    def _current_soundtrack(self) -> Soundtrack | None:
        data = self._soundtrack.currentData(_SOUNDTRACK_ROLE)
        return data if isinstance(data, Soundtrack) else None

    def _store_assignment(self, soundtrack_url: str, track_rttid: str, provenance: Provenance) -> None:
        project = self._state.project
        existing = self._assignment()
        locked = existing.locked if existing is not None else False
        project.assignments = [
            assignment
            for assignment in project.assignments
            if assignment.theme_name != self._system.theme_name
        ]
        project.assignments.append(
            Assignment(
                theme_name=self._system.theme_name,
                soundtrack_url=soundtrack_url,
                track_rttid=track_rttid,
                provenance=provenance,
                locked=locked,
            )
        )

    # ----------------------------------------------------------------- refresh
    def refresh(self) -> None:
        """Rebuild both pickers from the current assignment without signalling."""
        self._refreshing = True
        try:
            assignment = self._assignment()
            soundtrack = self._soundtrack_for(assignment)
            self._populate_soundtracks(assignment)
            self._populate_tracks(soundtrack, assignment)
            locked = bool(assignment is not None and assignment.locked)
            self._lock.blockSignals(True)
            self._lock.setChecked(locked)
            self._lock.blockSignals(False)
            self._update_lock_button(locked, assignment is not None)
            self._update_play_button()
        finally:
            self._refreshing = False

    def _populate_soundtracks(self, assignment: Assignment | None) -> None:
        combo = self._soundtrack
        combo.blockSignals(True)
        combo.clear()

        soundtracks = self._soundtracks()
        matching = assign.section1_soundtracks(self._system, soundtracks)
        matching_urls = {soundtrack.url for soundtrack in matching}
        others = [soundtrack for soundtrack in soundtracks if soundtrack.url not in matching_urls]

        _add_group_header(combo, "— Matched games —")
        if matching:
            for soundtrack in matching:
                combo.addItem(soundtrack.title, soundtrack)
                combo.setItemData(combo.count() - 1, soundtrack.title, Qt.ItemDataRole.ToolTipRole)
        else:
            _add_empty_placeholder(combo, "No matched soundtracks")

        _add_group_header(combo, "— All other games —")
        if others:
            for soundtrack in others:
                combo.addItem(soundtrack.title, soundtrack)
                combo.setItemData(combo.count() - 1, soundtrack.title, Qt.ItemDataRole.ToolTipRole)
        else:
            _add_empty_placeholder(combo, "No other soundtracks")

        index = _index_for_soundtrack(combo, assignment.soundtrack_url) if assignment else -1
        combo.setCurrentIndex(index)
        combo.blockSignals(False)

    def _populate_tracks(self, soundtrack: Soundtrack | None, assignment: Assignment | None) -> None:
        combo = self._track
        combo.blockSignals(True)
        combo.clear()
        if soundtrack is not None:
            for track in soundtrack.tracks:
                combo.addItem(_track_label(track), track.rttid)
                combo.setItemData(
                    combo.count() - 1,
                    f"{track.title}\nRating: {ranking.effective_score(track):.1f}",
                    Qt.ItemDataRole.ToolTipRole,
                )
        index = -1
        if assignment is not None and combo.count() > 0:
            index = combo.findData(assignment.track_rttid)
        combo.setCurrentIndex(index)
        combo.setEnabled(combo.count() > 0)
        combo.blockSignals(False)

    # ------------------------------------------------------------------ slots
    def _on_soundtrack_changed(self, index: int) -> None:
        if self._refreshing or index < 0:
            return
        soundtrack = self._soundtrack.itemData(index, _SOUNDTRACK_ROLE)
        if not isinstance(soundtrack, Soundtrack):
            return
        best_track = ranking.highest_scored_track(soundtrack.tracks)
        self._store_assignment(
            soundtrack.url,
            best_track.rttid if best_track is not None else "",
            Provenance.MANUAL,
        )
        logger.info(
            "systems: %r -> soundtrack %r, best track %r",
            self._system.theme_name,
            soundtrack.title,
            best_track.title if best_track is not None else None,
        )
        self.refresh()
        self.assignmentChanged.emit()

    def _on_track_changed(self, index: int) -> None:
        if self._refreshing or index < 0:
            return
        soundtrack = self._current_soundtrack()
        rttid = self._track.itemData(index)
        if soundtrack is None or not rttid:
            return
        self._store_assignment(soundtrack.url, str(rttid), Provenance.MANUAL)
        logger.info(
            "systems: %r track set to rttid=%s", self._system.theme_name, rttid
        )
        self._update_play_button()
        self.assignmentChanged.emit()

    def _on_lock_toggled(self, checked: bool) -> None:
        if self._refreshing:
            return
        assignment = self._assignment()
        if assignment is None:
            self._lock.blockSignals(True)
            self._lock.setChecked(False)
            self._lock.blockSignals(False)
            self._update_lock_button(False, False)
            return
        assignment.locked = checked
        self._update_lock_button(checked, True)
        logger.info(
            "systems: lock=%s for %r", checked, self._system.theme_name
        )
        self.assignmentChanged.emit()

    def _on_favourite_toggled(self, checked: bool) -> None:
        if self._refreshing:
            return
        self._system.favourite = checked
        self._favourite.setText("★" if checked else "☆")
        self._favourite.setToolTip(
            "Remove this system from favourites"
            if checked
            else "Add this system to favourites"
        )
        self._favourite.setAccessibleName(
            f"{'Unfavourite' if checked else 'Favourite'} {self._system.theme_name}"
        )
        logger.info(
            "systems: favourite=%s for %r", checked, self._system.theme_name
        )
        self.favouriteToggled.emit(self._system.theme_name, checked)

    def _on_play_clicked(self) -> None:
        rttid = self._track.currentData()
        if rttid:
            self._player.toggle(str(rttid))

    def _on_player_state_changed(self, _playing: bool, _rttid: str) -> None:
        self._update_play_button()

    def _on_player_preparing(self, rttid: str) -> None:
        current = self._track.currentData()
        if current and str(current) == rttid:
            self._play.setText("…")
            self._play.setToolTip("Downloading track…")

    def _on_player_error(self, _message: str) -> None:
        self._update_play_button()

    def _update_play_button(self) -> None:
        rttid = self._track.currentData()
        rttid_text = str(rttid) if rttid else ""
        playing = (
            bool(rttid_text)
            and self._player.current_rttid == rttid_text
            and self._player.is_playing
        )
        self._play.setText("⏸" if playing else "▶")
        self._play.setEnabled(bool(rttid_text))
        self._play.setToolTip(
            "Pause preview" if playing else "Preview this track from the cache"
        )
        self._play.setAccessibleName(
            f"{'Pause' if playing else 'Preview'} track for {self._system.theme_name}"
        )

    def _update_lock_button(self, locked: bool, enabled: bool) -> None:
        self._lock.setText("🔒" if locked else "🔓")
        self._lock.setEnabled(enabled)
        self._lock.setToolTip(
            "Automatic assignment keeps this system's current choice"
            if locked
            else "Lock this system so automatic assignment keeps the current choice"
        )
        self._lock.setAccessibleName(
            f"{'Unlock' if locked else 'Lock'} soundtrack for {self._system.theme_name}"
        )

    def _on_remove_clicked(self) -> None:
        self.removeRequested.emit(self._system.theme_name)


class _SystemSection(QWidget):
    """A section header followed by one :class:`SystemCell` per system."""

    removeRequested = Signal(str)
    favouriteToggled = Signal(str, bool)
    assignmentChanged = Signal()

    def __init__(
        self,
        title: str,
        systems: list[ThemeSystem],
        state,
        player: TrackPlayer,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.cells: list[SystemCell] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QLabel(f"{title} ({len(systems)})")
        header_font = header.font()
        header_font.setBold(True)
        header.setFont(header_font)
        header.setContentsMargins(8, 8, 8, 4)

        header.setAutoFillBackground(True)
        header_palette = header.palette()
        header_palette.setColor(
            QPalette.ColorRole.Window, header_palette.button().color()
        )
        header.setPalette(header_palette)

        layout.addWidget(header)

        if not systems:
            empty = QLabel("No systems in this section.")
            empty.setContentsMargins(8, 6, 8, 10)
            empty.setEnabled(False)
            layout.addWidget(empty)
            return

        for index, system in enumerate(systems):
            cell = SystemCell(
                state, system, player, striped=index % 2 == 1, parent=self
            )
            cell.removeRequested.connect(self.removeRequested)
            cell.favouriteToggled.connect(self.favouriteToggled)
            cell.assignmentChanged.connect(self.assignmentChanged)
            layout.addWidget(cell)
            self.cells.append(cell)


class SystemsTableView(QScrollArea):
    """Vertically scrollable table of project systems, grouped into two sections."""

    assignmentChanged = Signal()
    playerError = Signal(str)

    def __init__(
        self,
        state,
        cache_dir=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._cells: list[SystemCell] = []
        self._player = TrackPlayer(cache_dir, self)
        self._player.errorOccurred.connect(self.playerError)

        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.StyledPanel)

        self._container = QWidget()
        self._container.setStyleSheet(
            "QFrame#systemCell { border-bottom: 1px solid palette(mid); }"
        )
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self.setWidget(self._container)

        self.refresh()

    # ----------------------------------------------------------------- refresh
    def refresh(self) -> None:
        """Rebuild both sections from ``project.systems``."""
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._cells.clear()

        systems = list(self._state.project.systems)
        favourites = [system for system in systems if system.favourite]
        others = [system for system in systems if not system.favourite]

        for title, group in (
            ("Favourite systems", favourites),
            ("All other systems", others),
        ):
            section = _SystemSection(
                title, group, self._state, self._player, parent=self._container
            )
            section.removeRequested.connect(self._on_remove_requested)
            section.favouriteToggled.connect(self._on_favourite_toggled)
            section.assignmentChanged.connect(self.assignmentChanged)
            self._layout.addWidget(section)
            self._cells.extend(section.cells)

        self._layout.addStretch(1)
        logger.debug(
            "systems view: %d systems (%d favourites)", len(systems), len(favourites)
        )

    def refresh_assignments(self) -> None:
        """Refresh every cell's pickers in place (after automatic assignment)."""
        for cell in self._cells:
            cell.refresh()

    def shutdown(self) -> None:
        """Stop preview playback when the window closes."""
        self._player.stop()
        self._player.shutdown()

    # ------------------------------------------------------------------ slots
    def _on_remove_requested(self, theme_name: str) -> None:
        # Deferred: the emitting cell is deleted by refresh().
        QTimer.singleShot(0, lambda: self.confirm_remove(theme_name))

    def _on_favourite_toggled(self, theme_name: str, favourite: bool) -> None:
        # The cell already updated ``system.favourite``; re-section on the next
        # event-loop turn so the emitting widget is not destroyed mid-signal.
        logger.debug("systems view: re-sectioning after %r favourite=%s", theme_name, favourite)
        QTimer.singleShot(0, self.refresh)

    # ----------------------------------------------------------- system edits
    def confirm_remove(self, theme_name: str) -> None:
        """Ask whether to clear the system's choice or delete the system itself."""
        assignment = next(
            (
                candidate
                for candidate in self._state.project.assignments
                if candidate.theme_name == theme_name
            ),
            None,
        )

        box = QMessageBox(self)
        box.setWindowTitle("Remove system")
        box.setIcon(QMessageBox.Icon.Question)

        unassign_button = None
        if assignment is None:
            box.setText(f"Delete the system “{theme_name}”?")
            box.setInformativeText("This removes the system from the project.")
        else:
            box.setText(f"What should happen to “{theme_name}”?")
            box.setInformativeText(
                f"Current choice: {self._describe_assignment(assignment)}\n\n"
                "Unassign removes the soundtrack and track but keeps the system. "
                "Delete removes the system and its choice."
            )
            unassign_button = box.addButton(
                "Unassign soundtrack", QMessageBox.ButtonRole.AcceptRole
            )

        delete_button = box.addButton(
            "Delete system", QMessageBox.ButtonRole.DestructiveRole
        )
        cancel_button = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(cancel_button)
        box.setEscapeButton(cancel_button)
        box.exec()

        clicked = box.clickedButton()
        if unassign_button is not None and clicked is unassign_button:
            self.unassign_system(theme_name)
        elif clicked is delete_button:
            self.remove_system(theme_name)

    def _describe_assignment(self, assignment: Assignment) -> str:
        dataset = self._state.dataset
        if dataset is not None:
            for soundtrack in dataset.soundtracks:
                if soundtrack.url != assignment.soundtrack_url:
                    continue
                for track in soundtrack.tracks:
                    if track.rttid == assignment.track_rttid:
                        return f"{soundtrack.title} — {track.title}"
                return soundtrack.title
        return assignment.soundtrack_url

    def unassign_system(self, theme_name: str) -> None:
        """Clear one system's soundtrack/track choice but keep the system."""
        project = self._state.project
        before = len(project.assignments)
        project.assignments = [
            assignment
            for assignment in project.assignments
            if assignment.theme_name != theme_name
        ]
        if len(project.assignments) != before:
            logger.info("systems: unassigned soundtrack for %r", theme_name)
        self.refresh()

    def remove_system(self, theme_name: str) -> None:
        project = self._state.project
        before = len(project.systems)
        project.systems = [
            system for system in project.systems if system.theme_name != theme_name
        ]
        project.assignments = [
            assignment
            for assignment in project.assignments
            if assignment.theme_name != theme_name
        ]
        if len(project.systems) != before:
            logger.info("systems: removed system %r", theme_name)
        self.refresh()

    def remove_all(self) -> None:
        if not self._state.project.systems:
            return
        answer = QMessageBox.question(
            self,
            "Remove all systems",
            "Remove every system and its assignment from this project?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._state.project.systems.clear()
        self._state.project.assignments.clear()
        logger.info("systems: removed all systems")
        self.refresh()

    def reset_to_defaults(self) -> None:
        defaults = self._state.default_systems()
        if not defaults:
            QMessageBox.information(
                self,
                "Reset to default systems",
                "No default systems are available in the loaded dataset.",
            )
            return
        answer = QMessageBox.question(
            self,
            "Reset to default systems",
            f"Replace the current {len(self._state.project.systems)} systems "
            f"with the {len(defaults)} default systems?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        keep = {system.theme_name for system in defaults}
        self._state.project.systems = defaults
        self._state.project.assignments = [
            assignment
            for assignment in self._state.project.assignments
            if assignment.theme_name in keep
        ]
        logger.info("systems: reset to %d default systems", len(defaults))
        self.refresh()

    def add_system(self) -> None:
        project = self._state.project
        existing = {system.theme_name for system in project.systems}
        available = [
            system for system in self._state.theme_systems() if system.theme_name not in existing
        ]
        if not available:
            QMessageBox.information(
                self,
                "Add system",
                "Every system in the dataset is already in this project.",
            )
            return
        dialog = AddSystemDialog(available, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            selected = dialog.selected()
            if selected is not None:
                added = replace(
                    selected,
                    favourite=selected.favourite or dialog.as_favourite(),
                )
                project.systems.append(added)
                logger.info(
                    "systems: added system %r (favourite=%s)",
                    added.theme_name,
                    added.favourite,
                )
                self.refresh()
