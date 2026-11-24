from pathlib import Path

import pytest

from es_ost.prep import retrotracks
from es_ost.prep.retrotracks import (
    RetrotracksClient,
    parse_index,
    parse_size_mb,
    parse_tracks,
    resolve_track,
    track_url,
)

FIXTURES = Path(__file__).parent / "fixtures"


class _FakeResponse:
    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False


def _patch_urlopen(monkeypatch: pytest.MonkeyPatch, urls: list[str], body: str) -> None:
    def fake_urlopen(request, timeout=None):  # noqa: ANN001 - test stub
        urls.append(request.full_url)
        return _FakeResponse(body)

    monkeypatch.setattr(retrotracks.urllib.request, "urlopen", fake_urlopen)


def test_parse_index():
    html = (FIXTURES / "music-index.html").read_text(encoding="utf-8")
    soundtracks = parse_index(html)
    assert soundtracks
    assert soundtracks[0].sid == "888"
    assert soundtracks[0].title == ".hack"
    assert soundtracks[0].boxhash


def test_parse_tracks():
    html = (FIXTURES / "soundtrack-159.html").read_text(encoding="utf-8")
    tracks = parse_tracks(html)
    assert tracks[0].rttid == "5589"
    assert tracks[0].title == "Aitos ~ Temple"
    assert tracks[0].duration == "02:39"
    assert tracks[0].size_mb == pytest.approx(3.11)
    assert tracks[0].composer == "Yuzo Koshiro"


def test_parse_size_mb():
    assert parse_size_mb("3.11MB") == pytest.approx(3.11)
    assert parse_size_mb(None) is None


def test_track_url():
    assert track_url({"track_hbit": "a", "track_hash": "b"}) == "https://retro.sx/bank/a/b.mp3"


def test_index_is_never_cached(monkeypatch, tmp_path):
    urls: list[str] = []
    _patch_urlopen(monkeypatch, urls, "x")
    client = RetrotracksClient(min_delay=0.0, cache_dir=tmp_path)

    client.fetch_index()
    client.fetch_index()
    client.fetch_soundtrack("159")

    assert urls == [
        "https://retro.sx/music",
        "https://retro.sx/music",
        "https://retro.sx/music/159",
    ]
    assert len(list(tmp_path.glob("*.html"))) == 1


def test_fetch_without_cache_dir_always_hits_network(monkeypatch):
    urls: list[str] = []
    _patch_urlopen(monkeypatch, urls, "x")
    client = RetrotracksClient(min_delay=0.0)

    client.fetch_soundtrack("159")
    client.fetch_soundtrack("159")

    assert urls == ["https://retro.sx/music/159", "https://retro.sx/music/159"]


def test_soundtrack_tracks_reuses_cached_page(monkeypatch, tmp_path):
    urls: list[str] = []
    html = (FIXTURES / "soundtrack-159.html").read_text(encoding="utf-8")
    _patch_urlopen(monkeypatch, urls, html)
    client = RetrotracksClient(min_delay=0.0, cache_dir=tmp_path)

    first = client.soundtrack_tracks("159")
    second = client.soundtrack_tracks("159")

    assert first == second
    assert first[0].rttid == "5589"
    assert len(urls) == 1


def test_resolve_track_is_not_cached(monkeypatch):
    urls: list[str] = []
    _patch_urlopen(monkeypatch, urls, '{"track_hbit": "a", "track_hash": "b"}')

    resolve_track("5589")
    resolve_track("5589")

    assert len(urls) == 2
