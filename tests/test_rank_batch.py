import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "rank_batch.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("rank_batch", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _entry(url="/music/1", title="Sonic the Hedgehog", tracks=None):
    return {
        "soundtrack_url": url,
        "soundtrack_title": title,
        "matched_game_id": 1,
        "matched_game_name": title,
        "eligible_screenscraper_ids": [1],
        "eligible_platforms": ["Megadrive"],
        "tracks": tracks
        or [
            {"rttid": "10", "title": "Title Screen", "duration": "01:20", "theme_score": 1.4},
            {"rttid": "11", "title": "Green Hill Zone", "duration": "02:10", "theme_score": 0.0},
            {"rttid": "12", "title": "Game Over", "duration": "00:04", "theme_score": 0.0},
            {"rttid": "13", "title": "Short Sting", "duration": "00:20", "theme_score": 0.0},
        ],
    }


def test_duration_seconds_parses_minutes_and_hours():
    module = _load_script()
    assert module.duration_seconds("01:33") == 93
    assert module.duration_seconds("1:02:03") == 3723
    assert module.duration_seconds(None) is None
    assert module.duration_seconds("n/a") is None


def test_is_structurally_floored_flags_stings_and_cues_only():
    module = _load_script()

    assert module.is_structurally_floored({"title": "Game Over", "duration": "00:04"})
    assert module.is_structurally_floored({"title": "Stage Clear", "duration": "00:11"})
    assert module.is_structurally_floored({"title": "Theme", "duration": "00:20"})
    # A cue-length piece with a musical title, a short-but-named theme, and a
    # boss theme all stay eligible.
    assert not module.is_structurally_floored({"title": "Opening", "duration": "02:11"})
    assert not module.is_structurally_floored({"title": "Title", "duration": "00:48"})
    assert not module.is_structurally_floored({"title": "Boss Theme 1", "duration": "01:09"})


def test_take_batch_stops_at_the_track_budget():
    """One huge album must not blow up a batch meant to stay readable."""
    module = _load_script()
    big = _entry(url="/music/big", tracks=[{"rttid": str(n), "title": "t", "duration": "02:00"} for n in range(400)])
    small = _entry(url="/music/small", tracks=[{"rttid": str(n), "title": "t", "duration": "02:00"} for n in range(400, 415)])

    batch = module.take_batch([big, small], 0, 50, 50)

    # The first entry is always taken, even when it alone exceeds the budget:
    # an empty batch would stall the pass.
    assert [entry["soundtrack_url"] for entry in batch] == ["/music/big"]


def test_take_batch_keeps_filling_while_under_budget():
    module = _load_script()
    queue = [_entry(url=f"/music/{index}") for index in range(5)]

    batch = module.take_batch(queue, 0, 50, 1200)

    assert len(batch) == 5


def test_pending_entries_skips_fully_ranked_soundtracks():
    module = _load_script()
    ranked = _entry(url="/music/1")
    partial = _entry(
        url="/music/2",
        tracks=[
            {"rttid": "20", "title": "Title", "duration": "01:00", "theme_score": 1.4},
            {"rttid": "21", "title": "Game Over", "duration": "00:03", "theme_score": 0.0},
        ],
    )
    queue = [ranked, partial]

    entries = module.pending_entries(queue, {"10", "11", "12", "13"})

    assert [entry["soundtrack_url"] for entry in entries] == ["/music/2"]


def test_series_key_ignores_leading_articles():
    module = _load_script()
    assert module.series_key("Advance Wars 2: Black Hole Rising") == "advance wars"
    assert module.series_key("The Legend of Zelda") == "legend zelda"
    assert module.series_key("1944: The Loop Master") == "1944 loop"
    assert module.series_key("Super Metroid") == "super metroid"
    assert module.series_key("Mega Turrican") == "mega turrican"


def test_build_rankings_floors_unjudged_tracks_and_keeps_the_reason():
    module = _load_script()

    rankings = module.build_rankings(
        [_entry()], {"/music/1": {"pick": "the title theme", "scores": {"10": 80, "11": 40}}}
    )

    by_rttid = {item["rttid"]: item for item in rankings}
    assert by_rttid["10"]["llm_score"] == 80.0
    assert by_rttid["10"]["rank"] == 1
    assert by_rttid["10"]["reason"] == "the title theme"
    assert by_rttid["11"]["rank"] == 2
    assert by_rttid["12"]["llm_score"] == module.FLOOR
    assert by_rttid["13"]["llm_score"] == module.FLOOR
    # Floor ties keep the queue's own order, so a re-run is deterministic.
    assert [item["rttid"] for item in rankings] == ["10", "11", "12", "13"]


def test_build_rankings_rejects_an_unknown_rttid():
    module = _load_script()

    with pytest.raises(ValueError, match="rttids not in the queue entry"):
        module.build_rankings([_entry()], {"/music/1": {"pick": "x", "scores": {"999": 50}}})


def test_build_rankings_rejects_a_score_out_of_range():
    module = _load_script()

    with pytest.raises(ValueError, match="out of range"):
        module.build_rankings([_entry()], {"/music/1": {"pick": "x", "scores": {"10": 140}}})


def test_build_rankings_rejects_an_empty_or_unknown_soundtrack():
    module = _load_script()

    with pytest.raises(ValueError, match="no scored track"):
        module.build_rankings([_entry()], {"/music/1": {"pick": "x", "scores": {}}})
    with pytest.raises(ValueError, match="not in the queue"):
        module.build_rankings([_entry()], {"/music/9": {"pick": "x", "scores": {"10": 50}}})


def test_dump_marks_floored_tracks_and_skips_ranked_ones(tmp_path, capsys):
    module = _load_script()
    queue_path = tmp_path / "ranking_queue.json"
    queue_path.write_text(json.dumps([_entry()]), encoding="utf-8")

    exit_code = module.main(
        ["dump", "--queue", str(queue_path), "--data-dir", str(tmp_path / "data"), "--count", "5"]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "1 soundtracks, 4 tracks (2 structurally floored, 2 plausible)" in output
    assert "12 | Game Over | 00:04 | kw=0.0 [floor]" in output
    assert "10 | Title Screen | 01:20 | kw=1.4\n" in output


def test_dump_reports_when_nothing_is_left(tmp_path):
    module = _load_script()
    queue_path = tmp_path / "ranking_queue.json"
    queue_path.write_text("[]", encoding="utf-8")

    assert module.main(
        ["dump", "--queue", str(queue_path), "--data-dir", str(tmp_path / "data")]
    ) == 1


def test_title_key_absorbs_the_screen_scraper_spellings():
    """The report must not call an `ss` rewrite a mismatch."""
    module = _load_script()

    assert module.title_key("After Burner II") == module.title_key("After Burner 2")
    assert module.title_key("Command & Conquer") == module.title_key("Command And Conquer")
    assert module.title_key("Battletoads + Double Dragon") == module.title_key(
        "Battletoads Plus Double Dragon"
    )
    # Colon -> dash and the `, The` article ScreenScraper sorts to the end.
    assert module.title_key("The Legend of Zelda: Link's Awakening") == module.title_key(
        "Legend of Zelda, The - Link's Awakening"
    )
    # A sequel stays distinct.
    assert module.title_key("Final Fantasy VII") != module.title_key("Final Fantasy")


def test_same_title_recognises_variants_but_not_sequels():
    module = _load_script()

    assert module.same_title("Winter Olympics: Lillehammer'94", "Winter Olympics - Lillehammer '94")
    assert module.same_title("El Viento", "Viento, El")
    # ScreenScraper clips `noms` at 50 characters, mid-subtitle.
    assert module.same_title(
        "Battle Squadron: The Destruction of the Barrax Empire!",
        "Battle Squadron : The Destruction of the Barrax Em",
    )
    # A regional rename is not derivable from one name: only an alias saves it.
    assert not module.same_title("Star Fox", "Starwing")
    assert not module.same_title("Final Fantasy VII", "Final Fantasy")
    assert not module.same_title("Mega Man 9", "Mega Man")
    # A different sequel number is a different game, even at 98 similarity.
    assert not module.same_title("Super Mario Bros. 3", "Super Mario Bros. 35")
    assert not module.same_title("Mega Man X5", "Mega Man X")
    # ... while the same number spelled differently is not.
    assert module.same_title("Final Fantasy VII", "Final Fantasy 7")
    assert module.same_title("Winter Olympics: Lillehammer'94", "Winter Olympics - Lillehammer 94")


def test_cached_game_titles_reads_every_regional_name(tmp_path):
    module = _load_script()
    cache = tmp_path / ".cache"
    cache.mkdir()
    body = {
        "header": {"commandRequested": "https://api.screenscraper.fr/api2/jeuInfos.php?gameid=19196"},
        "response": {
            "jeu": {
                "noms": [
                    {"region": "ss", "text": "Starwing"},
                    {"region": "us", "text": "Star Fox"},
                ]
            }
        },
    }
    (cache / "entry.json").write_text(json.dumps(body), encoding="utf-8")
    (cache / "broken.json").write_text("not json", encoding="utf-8")

    assert module._cached_game_titles(tmp_path) == {19196: ["Starwing", "Star Fox"]}


def _dataset(tmp_path, soundtracks, games):
    from es_ost.core.dataset import Dataset, save_dataset

    dataset = Dataset(version="test", build_date="2026-01-01T00:00:00+00:00")
    dataset.games_by_platform = {1: games}
    dataset.soundtracks = soundtracks
    save_dataset(dataset, tmp_path)
    return tmp_path


def _ranked_soundtrack(url, title, game_id):
    from es_ost.core.models import RankStatus, Soundtrack, Track

    return Soundtrack(
        url=url,
        title=title,
        matched_game_id=game_id,
        tracks=[
            Track(rttid="1", title="Title", llm_score=70, rank_status=RankStatus.LLM),
            Track(rttid="2", title="Stage", llm_score=20, rank_status=RankStatus.LLM),
        ],
    )


def test_mismatch_rows_keeps_only_soundtracks_matched_to_another_game(tmp_path):
    from es_ost.core.models import Game

    module = _load_script()
    data_dir = _dataset(
        tmp_path,
        [
            _ranked_soundtrack("/music/1", "Star Fox", 19196),
            _ranked_soundtrack("/music/2", "After Burner II", 198),
            _ranked_soundtrack("/music/3", "Mega Man 9", 1274),
        ],
        [
            Game(id=19196, name="Starwing"),
            Game(id=198, name="After Burner 2"),
            Game(id=1274, name="Mega Man"),
        ],
    )
    # The `ss` name of the Star Fox entry is the European one; its regional
    # `noms` still spell "Star Fox", which is what must save it.
    cache = data_dir / ".cache"
    cache.mkdir(exist_ok=True)
    (cache / "fox.json").write_text(
        json.dumps(
            {
                "header": {
                    "commandRequested": "https://api.screenscraper.fr/api2/jeuInfos.php?gameid=19196"
                },
                "response": {"jeu": {"noms": [{"region": "us", "text": "Star Fox"}]}},
            }
        ),
        encoding="utf-8",
    )

    ranked, to_fix, recognised, unchecked = module.mismatch_rows(data_dir)

    assert ranked == 3
    assert [row[0] for row in to_fix] == ["/music/3"]
    assert to_fix[0][2] == "Mega Man"
    # Star Fox is saved by its regional name; After Burner II never gets that far
    # because its `ss` spelling already folds onto the title.
    assert recognised == 1
    assert unchecked == 0


def test_name_tokens_ignore_html_entities_and_release_suffixes():
    module = _load_script()

    # ScreenScraper escapes `&` in some names.
    assert module.title_key("Arcanum: Of Steamworks and Magick Obscura") == module.title_key(
        "Arcanum - Of Steamworks &#x26; Magick Obscura"
    )
    # Its `ss` names append "Version" to a release title that has none.
    assert module.title_key("Pokémon Crystal") == module.title_key("Pokémon - Crystal Version")
    # ... but a real edition marker still separates two releases.
    assert module.title_key("Tetris") != module.title_key("Tetris Special Edition")


def test_mismatch_rows_falls_back_to_the_search_cache_and_counts_the_rest(tmp_path):
    module = _load_script()
    data_dir = _dataset(
        tmp_path,
        [
            _ranked_soundtrack("/music/1", "Star Fox", 500),
            _ranked_soundtrack("/music/2", "Nowhere", 501),
        ],
        [],
    )
    # No `games_by_platform` entry for either id: the search response is the only
    # place the regional titles live.
    cache = data_dir / ".cache"
    cache.mkdir(exist_ok=True)
    (cache / "search.json").write_text(
        json.dumps(
            {
                "header": {
                    "commandRequested": (
                        "https://api.screenscraper.fr/api2/jeuRecherche.php?recherche=star%20fox"
                    )
                },
                "response": {
                    "jeux": [
                        {"id": "500", "noms": [{"region": "ss", "text": "Starwing"},
                                               {"region": "us", "text": "Star Fox"}]}
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    ranked, to_fix, recognised, unchecked = module.mismatch_rows(data_dir)

    assert ranked == 2
    # /music/1 is the right game under its US title, so nothing is reported;
    # /music/2's game has no title anywhere, so it can only be counted.
    assert to_fix == []
    assert unchecked == 1


def test_mismatches_report_moves_the_acknowledged_rows(tmp_path, capsys):
    from es_ost.core.models import Game

    module = _load_script()
    data_dir = _dataset(
        tmp_path,
        [_ranked_soundtrack("/music/1", "Mega Man 9", 1274)],
        [Game(id=1274, name="Mega Man")],
    )
    output = tmp_path / "report.md"
    acknowledged = tmp_path / "ok.tsv"
    acknowledged.write_text("/music/1\tpicked the Mega Man entry on purpose\n", encoding="utf-8")

    exit_code = module.main(
        [
            "mismatches",
            "--data-dir",
            str(data_dir),
            "--output",
            str(output),
            "--ignored",
            str(acknowledged),
        ]
    )
    report = output.read_text(encoding="utf-8")

    assert exit_code == 0
    assert "0 of the 1 ranked soundtracks are matched to a different game" in report
    assert "picked the Mega Man entry on purpose" in report
    assert "1 confirmed" in capsys.readouterr().out
