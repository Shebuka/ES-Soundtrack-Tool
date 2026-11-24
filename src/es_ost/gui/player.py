"""Inline audio preview player backed by the on-disk track cache.

The player never streams from Retrotracks directly: the first play of a track
downloads its source MP3 into the shared cache (the same one the exporter uses),
and every later play reads that local file. Downloads run on a daemon worker
thread so the UI stays responsive.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from es_ost import downloader

logger = logging.getLogger(__name__)


class _PrepareWorker(QObject):
    """Ensure a track's MP3 is cached, then report the local path.

    A plain daemon thread is used instead of ``QThread`` so quitting the app
    never has to wait for a stalled download. The queued signal hops back to the
    GUI thread safely.
    """

    ready = Signal(str, str)

    def __init__(self, rttid: str, cache_dir: Path | str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rttid = rttid
        self._cache_dir = cache_dir

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            path = downloader.ensure_cached_mp3(self._rttid, self._cache_dir)
            self.ready.emit(self._rttid, str(path))
        except Exception:  # noqa: BLE001 - surfaced to the UI as a status message
            logger.exception("player: failed to cache rttid=%s", self._rttid)
            self.ready.emit(self._rttid, "")


class TrackPlayer(QObject):
    """One shared QMediaPlayer for the whole table.

    ``stateChanged(playing, rttid)`` lets every cell decide whether its own
    play/pause icon should show pause (only the currently playing track does).
    """

    stateChanged = Signal(bool, str)
    preparing = Signal(str)
    errorOccurred = Signal(str)

    def __init__(
        self,
        cache_dir: Path | str | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._cache_dir = cache_dir or downloader.default_cache_dir()
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._player.setAudioOutput(self._audio)
        self._player.playbackStateChanged.connect(self._on_playback_state_changed)
        self._player.errorOccurred.connect(self._on_player_error)
        self._current_rttid = ""
        self._pending_rttid = ""
        self._workers: set[_PrepareWorker] = set()

    @property
    def current_rttid(self) -> str:
        return self._current_rttid

    @property
    def is_playing(self) -> bool:
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    # ------------------------------------------------------------------ control
    def toggle(self, rttid: str) -> None:
        """Play, pause or resume ``rttid`` from cache (downloading on a miss)."""
        rttid = str(rttid)
        if not rttid:
            return
        state = self._player.playbackState()
        if rttid == self._current_rttid:
            if state == QMediaPlayer.PlaybackState.PlayingState:
                logger.info("player: pausing rttid=%s", rttid)
                self._player.pause()
                return
            if state == QMediaPlayer.PlaybackState.PausedState:
                logger.info("player: resuming rttid=%s", rttid)
                self._player.play()
                return
        self._start(rttid)

    def stop(self) -> None:
        self._pending_rttid = ""
        self._current_rttid = ""
        self._player.stop()
        self.stateChanged.emit(False, "")

    def shutdown(self) -> None:
        """Stop playback and abandon any in-flight downloads at app exit."""
        logger.info("player: shutting down")
        self._pending_rttid = ""
        self._current_rttid = ""
        self._player.stop()
        self._workers.clear()

    # ------------------------------------------------------------------ internals
    def _start(self, rttid: str) -> None:
        self._pending_rttid = rttid
        self._current_rttid = ""
        self._player.stop()
        self.stateChanged.emit(False, "")

        cached = downloader.track_cache_path(rttid, self._cache_dir)
        if downloader.is_cached(cached):
            logger.info("player: cache hit for rttid=%s", rttid)
            self._play_path(rttid, cached)
            return

        logger.info("player: downloading rttid=%s for preview", rttid)
        self.preparing.emit(rttid)
        worker = _PrepareWorker(rttid, self._cache_dir, self)
        worker.ready.connect(self._on_prepared)
        self._workers.add(worker)
        worker.start()

    def _on_prepared(self, rttid: str, path_text: str) -> None:
        worker = self.sender()
        if isinstance(worker, _PrepareWorker):
            self._workers.discard(worker)
            worker.deleteLater()

        if rttid != self._pending_rttid:
            logger.debug("player: ignoring stale download for rttid=%s", rttid)
            return
        if not path_text:
            self.errorOccurred.emit(f"Could not download track {rttid}")
            return
        self._play_path(rttid, Path(path_text))

    def _play_path(self, rttid: str, path: Path) -> None:
        logger.info("player: playing rttid=%s from %s", rttid, path)
        self._current_rttid = rttid
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        self._player.play()

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.stateChanged.emit(playing, self._current_rttid)

    def _on_player_error(self, *args) -> None:
        message = args[-1] if args and isinstance(args[-1], str) else "Playback failed"
        logger.warning("player: media error: %s", message)
        self.errorOccurred.emit(message)
