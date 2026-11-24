from pathlib import Path

import pytest

from es_ost import downloader
from es_ost.core.models import Project
from es_ost.core.output import DEFAULT_MAX_TRACK_SECONDS, max_track_seconds


def _command(**kwargs):
    return downloader.build_transcode_command(Path("in.mp3"), Path("out.mp3"), ffmpeg="ffmpeg", **kwargs)


def test_long_track_is_normalized_cut_and_faded():
    command = _command(duration=180.0)
    assert command[command.index("-af") + 1] == "loudnorm=I=-16:TP=-1.5,afade=t=out:st=57:d=3"
    assert command[command.index("-to") + 1] == "60"
    assert command[command.index("-map_metadata") + 1] == "0"
    assert "-vn" in command
    assert command[command.index("-c:a") + 1] == "libmp3lame"
    assert command[command.index("-q:a") + 1] == str(downloader.MP3_VBR_QUALITY)
    assert "-b:a" not in command  # VBR, not a fixed bitrate
    assert command[command.index("-id3v2_version") + 1] == str(downloader.ID3V2_VERSION)
    assert command[command.index("-write_id3v1") + 1] == "1"
    assert command[0] == "ffmpeg"
    assert command[-1] == "out.mp3"


def test_mp3_is_the_default_format():
    command = _command(duration=10.0)
    assert command[command.index("-c:a") + 1] == "libmp3lame"
    assert command[command.index("-write_id3v1") + 1] == "1"


def test_ogg_uses_libvorbis_without_id3_muxer_options():
    command = _command(duration=10.0, audio_format="ogg")
    assert command[command.index("-c:a") + 1] == "libvorbis"
    assert command[command.index("-q:a") + 1] == str(downloader.VORBIS_QUALITY)
    assert "-id3v2_version" not in command
    assert "-write_id3v1" not in command


def test_wav_is_pinned_uncompressed_pcm():
    command = _command(duration=10.0, audio_format="wav")
    assert command[command.index("-c:a") + 1] == "pcm_s16le"
    assert command[command.index("-ar") + 1] == str(downloader.WAV_SAMPLE_RATE)
    assert "-q:a" not in command


def test_unknown_format_is_rejected_before_ffmpeg_runs():
    with pytest.raises(ValueError, match="unsupported audio format"):
        _command(duration=10.0, audio_format="flac")


def test_tags_are_written_after_the_mapped_source_metadata():
    command = _command(duration=10.0, tags={"title": "Main Theme", "artist": "Yoko Shimomura"})
    written = [command[i + 1] for i, part in enumerate(command) if part == "-metadata"]
    assert written == ["artist=Yoko Shimomura", "title=Main Theme"]
    assert command.index("-map_metadata") < command.index("-metadata")


def test_short_track_is_normalized_but_not_cut():
    command = _command(duration=12.5)
    assert "-to" not in command
    assert command[command.index("-af") + 1] == "loudnorm=I=-16:TP=-1.5"


def test_unknown_duration_is_treated_as_long():
    command = _command(duration=None)
    assert command[command.index("-to") + 1] == "60"
    assert "afade=t=out" in command[command.index("-af") + 1]


def test_max_seconds_none_disables_cut_and_fade():
    command = _command(duration=180.0, max_seconds=None)
    assert "-to" not in command
    assert "afade" not in command[command.index("-af") + 1]


def test_custom_max_seconds_moves_the_cut_and_fade():
    command = _command(duration=180.0, max_seconds=30)
    assert command[command.index("-to") + 1] == "30"
    assert "afade=t=out:st=27:d=3" in command[command.index("-af") + 1]


def test_normalize_false_drops_the_filter_chain():
    command = _command(duration=10.0, normalize=False)
    assert "-af" not in command


def test_probe_failure_degrades_to_unknown_duration(monkeypatch):
    monkeypatch.setattr(downloader.subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError()))
    assert downloader.probe_duration(Path("in.mp3")) is None


def test_project_setting_overrides_the_default():
    assert max_track_seconds(Project()) == DEFAULT_MAX_TRACK_SECONDS
    assert max_track_seconds(Project(output_settings={"max_track_seconds": 30})) == 30.0
    assert max_track_seconds(Project(output_settings={"max_track_seconds": None})) is None
    assert max_track_seconds(Project(output_settings={"max_track_seconds": 0})) is None
    assert max_track_seconds(Project(output_settings={"max_track_seconds": "junk"})) == DEFAULT_MAX_TRACK_SECONDS


def test_default_cache_dir_honours_the_environment_override(monkeypatch, tmp_path):
    monkeypatch.setenv("ES_OST_CACHE_DIR", str(tmp_path))
    assert downloader.default_cache_dir() == tmp_path


def test_ensure_cached_mp3_downloads_once(monkeypatch, tmp_path):
    calls: list[str] = []

    def fake_download(rttid, dest, *, timeout=60.0):
        calls.append(rttid)
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"ID3-audio")
        return Path(dest)

    monkeypatch.setattr(downloader, "download_mp3", fake_download)

    first = downloader.ensure_cached_mp3("8389", tmp_path)
    second = downloader.ensure_cached_mp3("8389", tmp_path)

    assert first == second
    assert first.read_bytes() == b"ID3-audio"
    assert calls == ["8389"]
    assert not list(tmp_path.glob("*.part"))


def test_export_transcodes_from_the_cached_mp3_and_keeps_it(monkeypatch, tmp_path):
    cached = tmp_path / "8389.mp3"
    cached.write_bytes(b"ID3-audio")
    seen: dict[str, object] = {}

    monkeypatch.setattr(
        downloader, "ensure_cached_mp3", lambda rttid, cache_dir, *, timeout=60.0: cached
    )

    def fake_transcode(src, dest, **kwargs):
        seen["src"] = Path(src)
        seen["tags"] = kwargs.get("tags")
        seen["format"] = kwargs.get("audio_format")
        Path(dest).write_bytes(b"ID3")
        return Path(dest)

    monkeypatch.setattr(downloader, "transcode_audio", fake_transcode)

    output = downloader.download_and_transcode(
        "8389",
        tmp_path / "snes.ogg",
        audio_format="ogg",
        tags={"title": "Main Theme", "artist": "Yoko Shimomura"},
        cache_dir=tmp_path,
    )

    assert output.read_bytes() == b"ID3"
    assert seen["src"] == cached
    assert seen["tags"] == {"title": "Main Theme", "artist": "Yoko Shimomura"}
    assert seen["format"] == "ogg"
    assert cached.exists()  # the cache survives the export
