"""Refresh data dialog: verify user login and read quota."""

from __future__ import annotations

import logging

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from es_ost.core.env_loader import EnvironmentLoadError, load_dev_credentials
from es_ost.prep.screenscraper import ScreenScraperClient

logger = logging.getLogger(__name__)


class _LoginThread(QThread):
    """Run ScreenScraper login verification off the UI thread."""

    result = Signal(str, object)

    def __init__(self, client: ScreenScraperClient, parent=None) -> None:
        super().__init__(parent)
        self._client = client

    def run(self) -> None:
        logger.info("refresh: verifying ScreenScraper login")
        try:
            quota = self._client.verify_login()
            logger.info("refresh: login OK — quota: %s", quota)
            self.result.emit("ok", quota)
        except Exception as exc:  # noqa: BLE001 - surface any error to the UI
            logger.exception("refresh: login failed")
            self.result.emit("error", exc)


class RefreshDialog(QDialog):
    """Collect the user's ScreenScraper credentials and verify login."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Refresh data")

        self._ssid = QLineEdit()
        self._sspassword = QLineEdit()
        self._sspassword.setEchoMode(QLineEdit.EchoMode.Password)

        form = QFormLayout()
        form.addRow("ScreenScraper user (ssid)", self._ssid)
        form.addRow("ScreenScraper password", self._sspassword)

        self._status = QLabel("Enter your ScreenScraper credentials and verify login.")
        self._status.setWordWrap(True)

        verify_button = QPushButton("Verify login & read quota")
        verify_button.clicked.connect(self._verify)

        close_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self._status)
        layout.addWidget(verify_button)
        layout.addWidget(close_buttons)

        self._thread: _LoginThread | None = None

    def _client(self) -> ScreenScraperClient:
        dev = load_dev_credentials()
        return ScreenScraperClient(
            dev.devid,
            dev.devpassword,
            dev.softname,
            ssid=self._ssid.text().strip() or None,
            sspassword=self._sspassword.text().strip() or None,
        )

    def _verify(self) -> None:
        logger.info("refresh: starting login verification")
        self._status.setText("Verifying…")
        try:
            client = self._client()
        except EnvironmentLoadError as exc:
            logger.error("refresh: failed to load dev credentials: %s", exc)
            self._status.setText(str(exc))
            return
        self._thread = _LoginThread(client, self)
        self._thread.result.connect(self._on_result)
        self._thread.start()

    def _on_result(self, status: str, payload: object) -> None:
        if status == "ok":
            logger.info("refresh: login verification succeeded")
            self._status.setText(f"Login OK — quota: {payload}")
        else:
            logger.warning("refresh: login verification failed: %s", payload)
            self._status.setText(f"Login failed: {payload}")
