"""Tests for the release ffmpeg fetcher.

The script is loaded by path, like ``rank_batch``, so its module-level constants
stay import-safe. Every test replaces ``TARGETS`` with a local archive served
through a ``file://`` URL, so nothing touches the network.
"""

from __future__ import annotations

import hashlib
import importlib.util
import tarfile
import urllib.error
import zipfile
from email.message import Message
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "fetch_ffmpeg.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("fetch_ffmpeg", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def fetch_ffmpeg():
    return _load_script()


def _zip(path: Path, *members: str) -> Path:
    with zipfile.ZipFile(path, "w") as bundle:
        for member in members:
            bundle.writestr(member, b"binary")
    return path


def _tar(path: Path, member: str) -> Path:
    payload = path.parent / "payload"
    payload.write_bytes(b"binary")
    with tarfile.open(path, "w:xz") as bundle:
        bundle.add(payload, arcname=member)
    payload.unlink()
    return path


def _target(archive: Path, members: dict[str, str]) -> dict:
    return {
        "archives": [{"url": archive.as_uri(), "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}],
        "members": members,
        "license": "test build",
    }


def test_windows_member_is_installed_as_exe(fetch_ffmpeg, tmp_path, monkeypatch):
    # The LGPL bundle carries ffprobe next to ffmpeg; only ffmpeg is installed,
    # under the name Windows can actually execute.
    archive = _zip(tmp_path / "ffmpeg-win64.zip", "bin/ffmpeg.exe", "bin/ffprobe.exe")
    monkeypatch.setitem(
        fetch_ffmpeg.TARGETS, "test-windows", _target(archive, {"ffmpeg.exe": "ffmpeg.exe"})
    )

    dest = tmp_path / "assets"
    fetch_ffmpeg.fetch("test-windows", dest, force=False)

    assert (dest / "ffmpeg.exe").read_bytes() == b"binary"
    assert not (dest / "ffmpeg").exists()
    assert not (dest / "ffprobe.exe").exists()
    assert "sha256:" in (dest / "SOURCE.txt").read_text()


def test_only_the_wanted_member_is_extracted(fetch_ffmpeg, tmp_path, monkeypatch):
    archive = _zip(tmp_path / "lgpl.zip", "bin/ffmpeg", "bin/ffprobe")
    monkeypatch.setitem(fetch_ffmpeg.TARGETS, "test-linux", _target(archive, {"ffmpeg": "ffmpeg"}))

    dest = tmp_path / "assets"
    fetch_ffmpeg.fetch("test-linux", dest, force=False)

    assert (dest / "ffmpeg").read_bytes() == b"binary"
    assert not (dest / "ffprobe").exists()


def test_tarball_member_is_extracted(fetch_ffmpeg, tmp_path, monkeypatch):
    archive = _tar(tmp_path / "lgpl.tar.xz", "pkg/bin/ffmpeg")
    monkeypatch.setitem(fetch_ffmpeg.TARGETS, "test-tar", _target(archive, {"ffmpeg": "ffmpeg"}))

    dest = tmp_path / "assets"
    fetch_ffmpeg.fetch("test-tar", dest, force=False)

    assert (dest / "ffmpeg").read_bytes() == b"binary"


def test_existing_binary_needs_force(fetch_ffmpeg, tmp_path, monkeypatch):
    archive = _zip(tmp_path / "ffmpeg.zip", "bin/ffmpeg.exe", "bin/ffprobe.exe")
    monkeypatch.setitem(
        fetch_ffmpeg.TARGETS, "test-windows", _target(archive, {"ffmpeg.exe": "ffmpeg.exe"})
    )
    dest = tmp_path / "assets"
    dest.mkdir()
    (dest / "ffmpeg.exe").write_bytes(b"old")

    with pytest.raises(SystemExit, match="already contains"):
        fetch_ffmpeg.fetch("test-windows", dest, force=False)


def test_checksum_mismatch_is_fatal(fetch_ffmpeg, tmp_path, monkeypatch):
    archive = _zip(tmp_path / "ffmpeg.zip", "bin/ffmpeg.exe", "bin/ffprobe.exe")
    target = _target(archive, {"ffmpeg.exe": "ffmpeg.exe"})
    target["archives"][0]["sha256"] = "0" * 64
    monkeypatch.setitem(fetch_ffmpeg.TARGETS, "test-windows", target)

    with pytest.raises(SystemExit, match="SHA-256 mismatch"):
        fetch_ffmpeg.fetch("test-windows", tmp_path / "assets", force=False)


def test_a_missing_member_names_the_archive(fetch_ffmpeg, tmp_path, monkeypatch):
    archive = _zip(tmp_path / "ffmpeg.zip", "bin/something-else")
    monkeypatch.setitem(
        fetch_ffmpeg.TARGETS, "test-windows", _target(archive, {"ffmpeg.exe": "ffmpeg.exe"})
    )

    with pytest.raises(SystemExit, match="missing from"):
        fetch_ffmpeg.fetch("test-windows", tmp_path / "assets", force=False)


def _http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = Message()
    if retry_after:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://example.test/x", code, "throttled", headers, None)


def test_a_throttled_mirror_is_retried(fetch_ffmpeg, tmp_path, monkeypatch):
    # evermeet.cx answers 429 for shared GitHub runner IPs.
    calls: list[str] = []
    sleeps: list[float] = []

    def flaky(url, dest):
        calls.append(url)
        if len(calls) < 3:
            raise _http_error(429, retry_after="1")
        dest.write_bytes(b"payload")
        return hashlib.sha256(b"payload").hexdigest()

    monkeypatch.setattr(fetch_ffmpeg, "_download_once", flaky)
    monkeypatch.setattr(fetch_ffmpeg.time, "sleep", sleeps.append)

    digest = fetch_ffmpeg.download("https://example.test/x", tmp_path / "x")

    assert digest == hashlib.sha256(b"payload").hexdigest()
    assert len(calls) == 3
    assert sleeps == [30.0, 60.0]


def test_a_missing_file_is_not_retried(fetch_ffmpeg, tmp_path, monkeypatch):
    def missing(url, dest):
        raise _http_error(404)

    monkeypatch.setattr(fetch_ffmpeg, "_download_once", missing)
    monkeypatch.setattr(fetch_ffmpeg.time, "sleep", lambda seconds: pytest.fail("slept"))

    with pytest.raises(urllib.error.HTTPError):
        fetch_ffmpeg.download("https://example.test/x", tmp_path / "x")
