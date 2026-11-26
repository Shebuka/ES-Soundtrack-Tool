import sys
from pathlib import Path

from es_ost import paths


def test_checkout_root_is_the_repository():
    root = paths.app_root()
    assert (root / "pyproject.toml").is_file()
    assert paths.data_dir() == root / "data"
    assert paths.ffmpeg_dir() == root / "assets" / "ffmpeg"
    assert paths.is_frozen() is False


def test_pyinstaller_bundle_root_uses_meipass(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert paths.app_root() == tmp_path.resolve()
    assert paths.is_frozen() is True


def test_frozen_onedir_root_uses_the_executable(monkeypatch, tmp_path):
    binary = tmp_path / "es-soundtrack-tool"
    binary.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "executable", str(binary))
    assert paths.app_root() == Path(binary).resolve().parent


def test_frozen_log_file_is_per_user(monkeypatch, tmp_path):
    from es_ost import logging_setup

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    log = logging_setup.log_file_path()
    assert log == tmp_path / "state" / "es-soundtrack-tool" / "app.log"
    assert log.parent.is_dir()


def test_windows_log_and_cache_share_one_folder(monkeypatch, tmp_path):
    # %LOCALAPPDATA% has no cache convention of its own, so both live in one
    # "ES Soundtrack Tool" folder instead of two spellings of the name.
    from es_ost import logging_setup

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert paths.user_log_dir() == tmp_path / "Local" / "ES Soundtrack Tool"
    assert paths.user_cache_root() == paths.user_log_dir()
    assert logging_setup.log_file_path() == paths.user_log_dir() / "app.log"
    assert paths.user_log_dir().is_dir()


def test_macos_keeps_logs_and_caches_apart(monkeypatch, tmp_path):
    from es_ost import logging_setup

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert logging_setup.log_file_path() == (
        tmp_path / "Library" / "Logs" / "ES Soundtrack Tool" / "app.log"
    )
    assert paths.user_cache_root() == tmp_path / "Library" / "Caches" / "ES Soundtrack Tool"


def test_linux_cache_follows_xdg(monkeypatch, tmp_path):
    from es_ost import downloader

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("ES_OST_CACHE_DIR", raising=False)
    assert downloader.default_cache_dir() == tmp_path / "cache" / "es-soundtrack-tool" / "tracks"
