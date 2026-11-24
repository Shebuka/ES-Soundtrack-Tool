from pathlib import Path

from es_ost import downloader
from es_ost.core import output as output_module
from es_ost.core.dataset import Dataset
from es_ost.core.models import Assignment, Project, Soundtrack, Track
from es_ost.core.output import (
    AUDIO_FORMATS,
    DEFAULT_AUDIO_FORMAT,
    AudioFormat,
    apply_alias_copies,
    resolve_audio_format,
    resolve_export_items,
)


def test_audio_formats_are_ordered_mp3_ogg_wav():
    assert AUDIO_FORMATS == (AudioFormat.MP3, AudioFormat.OGG, AudioFormat.WAV)
    assert DEFAULT_AUDIO_FORMAT is AudioFormat.MP3
    assert [audio_format.extension for audio_format in AUDIO_FORMATS] == ["mp3", "ogg", "wav"]


def test_audio_format_defaults_to_mp3():
    assert resolve_audio_format(Project()) is AudioFormat.MP3
    assert resolve_audio_format(Project(output_settings={"audio_format": "OGG"})) is AudioFormat.OGG
    assert resolve_audio_format(Project(output_settings={"audio_format": "flac"})) is AudioFormat.MP3


def test_resolve_export_items_skips_unknown():
    project = Project(
        assignments=[
            Assignment(theme_name="snes", soundtrack_url="/music/1", track_rttid="rtt-1"),
            Assignment(theme_name="bad", soundtrack_url="/music/9", track_rttid="nope"),
        ]
    )
    dataset = Dataset(
        version="v",
        build_date="d",
        soundtracks=[
            Soundtrack(
                url="/music/1",
                title="Chrono Trigger",
                tracks=[Track(rttid="rtt-1", title="Main Theme", composer="Yasunori Mitsuda")],
            )
        ],
    )
    items = resolve_export_items(project, dataset)

    assert [(item.theme_name, item.rttid) for item in items] == [("snes", "rtt-1")]
    assert items[0].track.title == "Main Theme"
    assert items[0].tags() == {
        "title": "Main Theme",
        "artist": "Yasunori Mitsuda",
        "album": "Chrono Trigger",
    }


def test_tags_fall_back_to_the_soundtrack_title_without_a_composer():
    project = Project(assignments=[Assignment(theme_name="snes", soundtrack_url="/music/1", track_rttid="rtt-1")])
    dataset = Dataset(
        version="v",
        build_date="d",
        soundtracks=[Soundtrack(url="/music/1", title="Chrono Trigger", tracks=[Track(rttid="rtt-1", title="A")])],
    )

    assert resolve_export_items(project, dataset)[0].tags()["artist"] == "Chrono Trigger"


def test_apply_alias_copies(tmp_path):
    (tmp_path / "snes.mp3").write_bytes(b"audio")
    copies = apply_alias_copies(tmp_path)
    assert (tmp_path / "sfc.mp3").exists()
    assert not (tmp_path / "famicom.mp3").exists()  # nes.mp3 doesn't exist yet
    assert [path.name for path in copies] == ["sfc.mp3"]


def test_alias_copies_follow_the_selected_format(tmp_path):
    (tmp_path / "snes.ogg").write_bytes(b"audio")
    copies = apply_alias_copies(tmp_path, AudioFormat.OGG)
    assert (tmp_path / "sfc.ogg").exists()
    assert not (tmp_path / "sfc.mp3").exists()
    assert [path.name for path in copies] == ["sfc.ogg"]


def test_export_reports_progress_for_each_assignment(monkeypatch, tmp_path):
    project = Project(
        assignments=[
            Assignment(theme_name="snes", soundtrack_url="/music/1", track_rttid="rtt-1"),
            Assignment(theme_name="nes", soundtrack_url="/music/1", track_rttid="rtt-2"),
        ]
    )
    dataset = Dataset(
        version="v",
        build_date="d",
        soundtracks=[
            Soundtrack(
                url="/music/1",
                title="X",
                tracks=[
                    Track(rttid="rtt-1", title="A"),
                    Track(rttid="rtt-2", title="B"),
                ],
            )
        ],
    )

    seen: list[dict[str, object]] = []

    def fake_download(rttid, dest_path, **kwargs):
        seen.append(
            {
                "rttid": rttid,
                "path": Path(dest_path),
                "tags": kwargs.get("tags"),
                "format": kwargs.get("audio_format"),
            }
        )
        Path(dest_path).write_bytes(b"mp3")
        return Path(dest_path)

    monkeypatch.setattr(downloader, "download_and_transcode", fake_download)
    events: list[tuple[int, int, str]] = []

    written = output_module.export(
        project,
        dataset,
        tmp_path,
        progress=lambda position, total, theme: events.append((position, total, theme)),
    )

    assert events == [(0, 2, ""), (1, 2, "snes"), (2, 2, "nes")]
    assert [path.name for path in written] == ["snes.mp3", "nes.mp3"]
    assert seen[0]["tags"] == {"title": "A", "artist": "X", "album": "X"}
    assert seen[0]["format"] == "mp3"


def test_export_honours_the_project_format(monkeypatch, tmp_path):
    project = Project(
        assignments=[Assignment(theme_name="snes", soundtrack_url="/music/1", track_rttid="rtt-1")],
        output_settings={"audio_format": "wav"},
    )
    dataset = Dataset(
        version="v",
        build_date="d",
        soundtracks=[Soundtrack(url="/music/1", title="X", tracks=[Track(rttid="rtt-1", title="A")])],
    )
    seen: list[tuple[Path, object]] = []

    def fake_download(rttid, dest_path, **kwargs):
        seen.append((Path(dest_path), kwargs.get("audio_format")))
        Path(dest_path).write_bytes(b"RIFF")
        return Path(dest_path)

    monkeypatch.setattr(downloader, "download_and_transcode", fake_download)

    written = output_module.export(project, dataset, tmp_path, alias_copy=True)

    assert [path.name for path in written] == ["snes.wav", "sfc.wav"]
    assert seen[0][0].name == "snes.wav"
    assert seen[0][1] == "wav"


def test_export_reports_zero_total_without_assignments(tmp_path):
    events: list[tuple[int, int, str]] = []

    written = output_module.export(
        Project(),
        Dataset(version="v", build_date="d"),
        tmp_path,
        progress=lambda position, total, theme: events.append((position, total, theme)),
    )

    assert written == []
    assert events == [(0, 0, "")]
