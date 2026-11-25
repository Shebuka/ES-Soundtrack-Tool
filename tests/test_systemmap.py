from es_ost import paths
from es_ost.core.models import ThemeKind
from es_ost.core.systemmap import (
    alias_targets,
    classify_kind,
    load_theme_systems,
    normalize_screenscraper,
    bundled_systems_json,
)


def test_normalize_screenscraper():
    assert normalize_screenscraper(4) == 4
    assert normalize_screenscraper("6") == 6
    assert normalize_screenscraper("") is None
    assert normalize_screenscraper(None) is None
    assert normalize_screenscraper("not-a-number") is None
    assert normalize_screenscraper(True) is None


def test_systems_json_lives_with_the_dataset():
    mapping = bundled_systems_json()
    assert mapping == paths.data_dir() / "systems.json"
    assert mapping.is_file()


def test_theme_systems_resolve_verified_ids():
    systems = {system.theme_name: system for system in load_theme_systems(bundled_systems_json())}
    assert systems["cps1"].screenscraper_id == 6
    assert systems["cps2"].screenscraper_id == 7
    assert systems["snes"].screenscraper_id == 4
    assert systems["snesh"].screenscraper_id == 202
    assert systems["gbc"].screenscraper_id == 10
    assert systems["gbch"].screenscraper_id == 10
    assert systems["ggh"].screenscraper_id == 21
    assert systems["megadrive-msu"].screenscraper_id == 1


def test_alias_targets():
    assert alias_targets() == {"snes": ["sfc"], "nes": ["famicom"]}


def test_classify_kind():
    assert classify_kind("snesh") is ThemeKind.HACK
    assert classify_kind("sfc") is ThemeKind.ALIAS
    assert classify_kind("snes-msu1") is ThemeKind.VARIANT
    assert classify_kind("snes") is ThemeKind.STANDARD
