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

    monkeypatch.setattr(logging_setup.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    log = logging_setup.log_file_path()
    assert log == tmp_path / "state" / "es-soundtrack-tool" / "app.log"
    assert log.parent.is_dir()
