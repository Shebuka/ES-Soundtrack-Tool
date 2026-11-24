from es_ost.prep.cache import CacheClearResult, PageCache, cache_key, clear_cache


def test_cache_key_is_stable_and_suffix_scoped():
    key = cache_key("https://retro.sx/music/159", "html")
    assert key == cache_key("https://retro.sx/music/159", "html")
    assert key.endswith(".html")
    assert key != cache_key("https://retro.sx/music/160", "html")


def test_disabled_cache_never_touches_disk():
    cache = PageCache(None)
    assert cache.enabled is False
    assert cache.directory is None
    assert cache.get("page.html") is None
    cache.put("page.html", "body")  # must be a no-op, not an error


def test_cache_roundtrip_leaves_no_temp_files(tmp_path):
    cache = PageCache(tmp_path)
    assert cache.enabled is True
    assert cache.get("page.html") is None

    cache.put("page.html", "<html>hi</html>")

    assert cache.get("page.html") == "<html>hi</html>"
    assert not list(tmp_path.glob("*.tmp"))


def test_undecodable_entry_counts_as_miss(tmp_path):
    cache = PageCache(tmp_path)
    (tmp_path / "bad.html").write_bytes(b"\xff\xfe\x00")

    assert cache.get("bad.html") is None


def test_clear_cache_removes_entries_and_nested_dirs(tmp_path):
    (tmp_path / "a.json").write_text("{}", encoding="utf-8")
    (tmp_path / "b.html").write_text("x" * 2048, encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "c.json").write_text("{}", encoding="utf-8")

    result = clear_cache(tmp_path)

    assert result.removed == 3
    assert result.freed_bytes == 2 + 2048
    assert list(tmp_path.iterdir()) == []


def test_clear_cache_missing_directory_is_noop(tmp_path):
    assert clear_cache(tmp_path / "absent") == CacheClearResult()
