"""New Project wizard: choose Empty or Default systems."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QRadioButton,
    QVBoxLayout,
)


class NewProjectDialog(QDialog):
    """Asks whether the new project starts empty or with default systems."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Project")
        self._empty = QRadioButton("Empty project")
        self._defaults = QRadioButton("Default systems (ArkOS / RetroArch easyroms)")
        self._defaults.setChecked(True)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("How would you like to start?"))
        layout.addWidget(self._empty)
        layout.addWidget(self._defaults)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def use_defaults(self) -> bool:
        return self._defaults.isChecked()
