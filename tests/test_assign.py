from es_ost.core.assign import auto_assign, section1_soundtracks
from es_ost.core.models import Game, Soundtrack, ThemeSystem, Track


def test_section1_filters_by_eligible_id():
    system = ThemeSystem(theme_name="snes", rom_folder="snes", screenscraper_id=4)
    soundtracks = [
        Soundtrack(url="/music/1", title="A", eligible_screenscraper_ids=[4]),
        Soundtrack(url="/music/2", title="B", eligible_screenscraper_ids=[1]),
    ]
    assert [soundtrack.url for soundtrack in section1_soundtracks(system, soundtracks)] == ["/music/1"]


def test_auto_assign_favourites_first_and_dedup_shared_id():
    systems = [
        ThemeSystem(theme_name="megadrive", rom_folder="megadrive", screenscraper_id=1),
        ThemeSystem(theme_name="snes", rom_folder="snes", screenscraper_id=4, favourite=True),
        ThemeSystem(theme_name="genesis", rom_folder="genesis", screenscraper_id=1),
    ]
    soundtracks = [
        Soundtrack(
            url="/music/1",
            title="A",
            eligible_screenscraper_ids=[1, 4],
            tracks=[Track(rttid="a1", title="Main Theme", theme_score=1.5)],
        ),
        Soundtrack(
            url="/music/2",
            title="B",
            eligible_screenscraper_ids=[1, 4],
            tracks=[Track(rttid="b1", title="Opening", theme_score=1.3)],
        ),
    ]
    games = {1: [Game(id=10, name="X", scrape_count=900)], 4: [Game(id=11, name="Y", scrape_count=1000)]}
    assignments = auto_assign(systems, soundtracks, games)

    by_theme = {assignment.theme_name: assignment.soundtrack_url for assignment in assignments}
    assert assignments[0].theme_name == "snes"  # favourite sorted first
    assert by_theme["megadrive"] != by_theme["genesis"]  # shared id still deduped


def test_auto_assign_skips_locked_systems():
    """A locked system keeps the user's choice, so automatic mode must not touch it."""
    systems = [
        ThemeSystem(theme_name="snes", rom_folder="snes", screenscraper_id=4),
        ThemeSystem(theme_name="nes", rom_folder="nes", screenscraper_id=3),
    ]
    soundtracks = [
        Soundtrack(
            url="/music/1",
            title="Shared",
            eligible_screenscraper_ids=[3, 4],
            tracks=[Track(rttid="a1", title="Main Theme", theme_score=1.0)],
        )
    ]
    games = {
        4: [Game(id=1, name="X", scrape_count=10)],
        3: [Game(id=2, name="Y", scrape_count=10)],
    }

    assignments = auto_assign(systems, soundtracks, games, locked_theme_names={"snes"})

    assert [assignment.theme_name for assignment in assignments] == ["nes"]


def test_auto_assign_treats_reserved_soundtracks_as_used():
    """A locked system's soundtrack must count as used for the remaining systems."""
    systems = [ThemeSystem(theme_name="gbc", rom_folder="gbc", screenscraper_id=10)]
    soundtracks = [
        Soundtrack(
            url="/music/dk",
            title="Donkey Kong Country",
            eligible_screenscraper_ids=[4, 10],
            tracks=[Track(rttid="dk1", title="DK Island Swing", theme_score=2.0)],
        ),
        Soundtrack(
            url="/music/other",
            title="Other",
            eligible_screenscraper_ids=[10],
            tracks=[Track(rttid="o1", title="Other Theme", theme_score=0.5)],
        ),
    ]
    games = {10: [Game(id=1, name="Y", scrape_count=10)]}

    assignments = auto_assign(
        systems,
        soundtracks,
        games,
        reserved_soundtrack_urls=["/music/dk"],
    )

    # The locked SNES choice is counted once, so GBC prefers the unused soundtrack.
    assert assignments[0].soundtrack_url == "/music/other"
