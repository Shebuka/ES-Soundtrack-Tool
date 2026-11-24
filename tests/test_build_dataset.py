import json
import logging
from pathlib import Path

import pytest

from es_ost.core.credentials import DevCredentials
from es_ost.core.dataset import Dataset, load_dataset, save_dataset
from es_ost.core.models import RankStatus, Soundtrack, ThemeSystem, Track
from es_ost.prep import build_dataset
from es_ost.prep.build_dataset import (
    RunStats,
    SoundtrackMatch,
    apply_rankings,
    build_games_for_soundtracks,
    build_ranking_queue,
    carry_forward,
    compute_provisional_tracks,
    flatten_rankings,
    match_soundtrack,
    search_candidates,
)
from es_ost.prep.retrotracks import ParsedSoundtrack, ParsedTrack
from es_ost.prep.screenscraper import (
    ApiClosedError,
    AuthError,
    InvalidResponseError,
    QuotaExceededError,
    ScreenScraperClient,
    ScreenScraperError,
)


def test_compute_provisional_tracks():
    tracks = compute_provisional_tracks(
        [ParsedTrack(rttid="1", title="Main Theme"), ParsedTrack(rttid="2", title="Ending")]
    )
    assert tracks[0].theme_score > 0
    assert tracks[1].theme_score == 0.0


def test_carry_forward_preserves_llm_scores():
    soundtrack = Soundtrack(
        url="/music/9",
        title="X",
        tracks=[Track(rttid="1", title="Main Theme"), Track(rttid="2", title="Ending")],
    )
    previous = Dataset(
        version="v",
        build_date="d",
        soundtracks=[
            Soundtrack(
                url="/music/9",
                title="X",
                tracks=[Track(rttid="1", title="Main Theme", llm_score=90.0, rank_status=RankStatus.LLM)],
            )
        ],
    )
    carry_forward([soundtrack], previous)

    assert soundtrack.tracks[0].rank_status is RankStatus.LLM
    assert soundtrack.tracks[0].llm_score == 90.0
    assert soundtrack.tracks[1].rank_status is RankStatus.PENDING


def test_build_ranking_queue_groups_pending_tracks_by_soundtrack():
    soundtrack = Soundtrack(
        url="/music/9",
        title="Sonic the Hedgehog",
        matched_game_id=10,
        eligible_screenscraper_ids=[4, 1],
        tracks=[
            Track(rttid="1", title="Title Screen", theme_score=1.5, rank_status=RankStatus.PENDING),
            Track(rttid="2", title="Green Hill Zone", rank_status=RankStatus.PENDING),
            Track(rttid="3", title="Spring Yard Zone", llm_score=80.0, rank_status=RankStatus.LLM),
        ],
    )

    queue = build_ranking_queue(
        [soundtrack],
        game_names={10: "Sonic the Hedgehog"},
        platform_names={1: "Megadrive", 4: "Super Nintendo"},
    )

    assert queue.queued_tracks == 2  # the already-ranked track is not re-queued
    assert queue.pending_tracks == 2
    assert queue.skipped_tracks == 0
    assert len(queue.entries) == 1
    entry = queue.entries[0]
    assert entry["soundtrack_url"] == "/music/9"
    assert entry["soundtrack_title"] == "Sonic the Hedgehog"
    assert entry["matched_game_id"] == 10
    assert entry["matched_game_name"] == "Sonic the Hedgehog"
    assert entry["eligible_screenscraper_ids"] == [1, 4]
    assert entry["eligible_platforms"] == ["Megadrive", "Super Nintendo"]
    assert [track["rttid"] for track in entry["tracks"]] == ["1", "2"]
    assert entry["tracks"][0]["theme_score"] == 1.5


def test_build_ranking_queue_skips_soundtracks_without_an_eligible_platform():
    """No candidates means no system can auto-assign it, so ranking is wasted."""
    unmatched = Soundtrack(
        url="/music/3",
        title=".hack",
        tracks=[Track(rttid="7", title="Airship Brigade", rank_status=RankStatus.PENDING)],
    )

    queue = build_ranking_queue([unmatched])

    assert queue.entries == []
    assert queue.skipped_soundtracks == 1
    assert queue.skipped_tracks == 1
    assert queue.pending_tracks == 1  # still pending: it is skipped, not ranked


def test_build_ranking_queue_keeps_a_loosely_matched_soundtrack():
    """``matched_game_id is None`` is not a reason to skip a soundtrack.

    The eligible set is the union of every candidate's platforms and is the only
    thing auto-assignment reads, so a soundtrack without a confident match is
    still usable — only an *empty* eligible set means unreachable.
    """
    loosely_matched = Soundtrack(
        url="/music/5",
        title="Some Game",
        matched_game_id=None,
        eligible_screenscraper_ids=[1],
        tracks=[Track(rttid="8", title="Main Theme", rank_status=RankStatus.PENDING)],
    )

    queue = build_ranking_queue([loosely_matched])

    assert queue.queued_tracks == 1
    assert queue.entries[0]["matched_game_id"] is None
    assert queue.entries[0]["matched_game_name"] is None
    assert queue.entries[0]["eligible_platforms"] == ["1"]  # unknown id stays as-is


def test_apply_rankings_sets_the_authoritative_score():
    soundtrack = Soundtrack(
        url="/music/9",
        title="X",
        tracks=[Track(rttid="1", title="Main Theme", rank_status=RankStatus.PENDING)],
    )

    applied = apply_rankings([soundtrack], {"1": 77.0})

    assert applied == 1
    assert soundtrack.tracks[0].llm_score == 77.0
    assert soundtrack.tracks[0].rank_status is RankStatus.LLM


def test_flatten_rankings_accepts_every_supported_shape():
    """A ranking run may write flat, as a map, or grouped like the queue."""
    flat = [{"rttid": "1", "llm_score": 90.0}, {"rttid": "2", "llm_score": 10}]
    mapping = {"1": 90.0, "2": 10}
    soundtrack_rooted = {
        "/music/9": [
            {"rttid": "1", "llm_score": 90.0, "rank": 1, "reason": "title screen"},
            {"rttid": "2", "llm_score": 10},
        ]
    }
    queue_shaped = [
        {"soundtrack_url": "/music/9", "tracks": [{"rttid": "1", "llm_score": 90.0}]}
    ]

    assert flatten_rankings(flat) == {"1": 90.0, "2": 10.0}
    assert flatten_rankings(mapping) == {"1": 90.0, "2": 10.0}
    assert flatten_rankings(soundtrack_rooted) == {"1": 90.0, "2": 10.0}
    assert flatten_rankings(queue_shaped) == {"1": 90.0}
    assert flatten_rankings([]) == {}


@pytest.mark.parametrize(
    "raw",
    [
        [{"rttid": "1", "llm_score": 101.0}],
        [{"rttid": "1", "llm_score": -1}],
        [{"rttid": "1", "llm_score": "high"}],
        [{"rttid": "1"}],
        [{"llm_score": 50.0}],
        ["not-an-object"],
        "not-a-file-shape",
    ],
)
def test_flatten_rankings_rejects_malformed_entries(raw):
    with pytest.raises(ValueError):
        flatten_rankings(raw)


def test_merge_rankings_accepts_a_soundtrack_rooted_file(monkeypatch, tmp_path):
    monkeypatch.setattr(
        build_dataset,
        "load_dev_credentials",
        lambda: DevCredentials("devid", "devpass", "soft"),
    )
    soundtrack = Soundtrack(
        url="/music/9",
        title="Sonic the Hedgehog",
        matched_game_id=10,
        eligible_screenscraper_ids=[1],
        tracks=[Track(rttid="1", title="Title Screen", rank_status=RankStatus.PENDING)],
    )
    save_dataset(Dataset(version="v", build_date="d", soundtracks=[soundtrack]), tmp_path)
    rankings_path = tmp_path / "rankings.json"
    rankings_path.write_text(
        json.dumps({"/music/9": [{"rttid": "1", "llm_score": 88.0}]}), encoding="utf-8"
    )

    args = build_dataset.build_parser().parse_args(
        ["merge-rankings", "--data-dir", str(tmp_path), "--rankings", str(rankings_path)]
    )
    assert args.func(args) == 0

    merged = load_dataset(tmp_path)
    assert merged.soundtracks[0].tracks[0].llm_score == 88.0
    assert merged.soundtracks[0].tracks[0].rank_status is RankStatus.LLM


@pytest.mark.parametrize("custom_cache_dir", [False, True])
def test_clear_cache_subcommand_removes_entries(monkeypatch, tmp_path, custom_cache_dir):
    """The subcommand uses <data-dir>/.cache unless --cache-dir overrides it."""
    monkeypatch.setattr(
        build_dataset,
        "load_dev_credentials",
        lambda: DevCredentials("devid", "devpass", "soft"),
    )
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "custom" if custom_cache_dir else data_dir / ".cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "abc.json").write_text("{}", encoding="utf-8")

    argv = ["clear-cache", "--data-dir", str(data_dir)]
    if custom_cache_dir:
        argv += ["--cache-dir", str(cache_dir)]
    args = build_dataset.build_parser().parse_args(argv)
    args.func(args)

    assert list(cache_dir.iterdir()) == []


def test_build_parser_reads_env_defaults(monkeypatch):
    monkeypatch.setattr(
        build_dataset,
        "load_dev_credentials",
        lambda: DevCredentials("devid", "devpass", "soft"),
    )
    args = build_dataset.build_parser().parse_args(["build"])
    assert args.devid == "devid"
    assert args.devpassword == "devpass"
    assert args.softname == "soft"


def test_game_from_jeu_sums_nbscrap():
    jeu = {
        "note": {"text": "18"},
        "roms": [{"nbscrap": "10"}, {"nbscrap": 5}, {"nbscrap": "0"}],
    }
    game = build_dataset._game_from_jeu(1, jeu)
    assert game.scrape_count == 15
    assert game.note == 18


def test_game_from_jeu_reads_the_dict_shaped_note():
    """jeuInfos sends ``note`` as ``{"text": "16"}`` or null, never a bare int."""
    jeu = {
        "noms": [{"region": "ss", "text": "Lemmings 2 - The Tribes"}],
        "note": {"text": "16"},
        "roms": [{"nbscrap": "4"}],
    }
    game = build_dataset._game_from_jeu(7, jeu)

    assert game.note == 16
    assert game.name == "Lemmings 2 - The Tribes"


def test_game_from_jeu_keeps_note_null_when_absent():
    game = build_dataset._game_from_jeu(7, {"note": None, "roms": []})
    assert game.note is None
    assert game.name == "7"  # no noms, no CSV row: fall back to the id


def test_platforms_from_systems_reads_the_nested_names():
    systems = [
        {
            "id": 1,
            "noms": {
                "nom_eu": "Megadrive",
                "nom_us": "Genesis",
                "nom_recalbox": "megadrive",
            },
        },
        {"id": 4, "noms": {"nom_us": "Super Nintendo"}},
        {"id": 9},
    ]
    platforms = build_dataset._platforms_from_systems(systems)

    assert platforms == {1: "Megadrive", 4: "Super Nintendo", 9: "9"}


class _StubClient(ScreenScraperClient):
    """ScreenScraperClient whose responses fail on demand (no network access)."""

    def __init__(
        self, *, search_error=None, game_errors=None, candidates=None, scrapes=None
    ):
        super().__init__("devid", "devpassword", "softname", min_delay=0.0)
        self._search_error = search_error
        self._game_errors = dict(game_errors or {})
        self._candidates = list(candidates or [])
        self._scrapes = dict(scrapes or {})
        self.fetched: list[int] = []

    def search_variants(self, name, systemeid=None):
        if self._search_error is not None:
            raise self._search_error
        return list(self._candidates)

    def game_info(self, gameid):
        self.fetched.append(gameid)
        error = self._game_errors.pop(gameid, None)
        if error is not None:
            raise error
        return {
            "note": {"text": "18"},
            "roms": [{"nbscrap": self._scrapes.get(gameid, 4)}],
        }


def _theme_system(screenscraper_id: int | None = 1, theme_name="Amiga", in_defaults=True):
    return ThemeSystem(
        theme_name=theme_name,
        rom_folder=theme_name.lower(),
        screenscraper_id=screenscraper_id,
        in_defaults=in_defaults,
    )


def _parsed():
    return ParsedSoundtrack(sid="42", title="Lemmings", boxhash="", url="/music/42")


def _soundtrack(url, matched_game_id, eligible_ids, title="Lemmings"):
    return Soundtrack(
        url=url,
        title=title,
        matched_game_id=matched_game_id,
        eligible_screenscraper_ids=list(eligible_ids),
    )


def test_search_candidates_degrades_on_transient_error():
    stats = RunStats()
    candidates = search_candidates(
        _StubClient(search_error=InvalidResponseError("<html>503</html>")),
        _parsed(),
        stats=stats,
    )

    assert candidates == []
    assert stats.unmatched_soundtracks == 1


@pytest.mark.parametrize(
    "error",
    [
        AuthError("bad creds"),
        ApiClosedError("api closed", 423),
        QuotaExceededError("quota exhausted", 430),
    ],
)
def test_search_candidates_reraises_fatal_error(error):
    stats = RunStats()
    with pytest.raises(type(error)):
        search_candidates(_StubClient(search_error=error), _parsed(), stats=stats)
    assert stats.unmatched_soundtracks == 0


def test_search_candidates_filters_the_low_score_tail():
    base = {"id": 10, "systemeid": 4, "noms": [{"region": "ss", "text": "Command & Conquer"}]}
    unrelated = {"id": 11, "systemeid": 9, "noms": [{"region": "ss", "text": "Sonic the Hedgehog"}]}
    parsed = ParsedSoundtrack(sid="7", title="Command & Conquer", boxhash="", url="/music/7")

    candidates = search_candidates(_StubClient(candidates=[base, unrelated]), parsed)

    assert [candidate["id"] for candidate in candidates] == [10]


def test_match_soundtrack_records_every_candidate_platform():
    """Matching is pure: the winner plus the platforms of every candidate."""
    nes = {"id": "10", "systemeid": 3, "noms": [{"region": "ss", "text": "Mega Man 2"}]}
    genesis = {"id": "11", "systemeid": 1, "noms": [{"region": "ss", "text": "Mega Man 2"}]}
    remake = {"id": "12", "systemeid": 20, "noms": [{"region": "ss", "text": "Mega Man 2 - Remake"}]}
    parsed = ParsedSoundtrack(sid="7", title="Mega Man 2", boxhash="", url="/music/7")

    soundtrack = match_soundtrack(parsed, [nes, genesis, remake])

    # jeuRecherche sends ids as strings; every int-keyed lookup needs an int.
    assert soundtrack.matched_game_id == 10
    assert isinstance(soundtrack.matched_game_id, int)
    assert soundtrack.match_confidence == 100.0
    assert soundtrack.eligible_screenscraper_ids == [1, 3, 20]


def _candidate(game_id, platform_id, title="Mega Man 2"):
    """A jeuRecherche candidate with one platform, as the API returns it."""
    return {
        "id": game_id,
        "noms": [{"region": "ss", "text": title}],
        "systeme": {"id": platform_id},
    }


def test_build_games_for_soundtracks_fetches_every_candidate_once(caplog):
    """Every filtered candidate is fetched once — not every game of a platform."""
    client = _StubClient()
    matches = [
        SoundtrackMatch(_soundtrack("/music/1", 10, [1, 3]), [_candidate(10, 3), _candidate(11, 1)]),
        # Same candidates again: ids must not be fetched twice.
        SoundtrackMatch(_soundtrack("/music/2", 11, [1]), [_candidate(10, 3), _candidate(11, 1)]),
        # Unmatched soundtrack: nothing to fetch.
        SoundtrackMatch(_soundtrack("/music/3", None, []), []),
    ]

    with caplog.at_level(logging.DEBUG, logger="es_ost.prep.build_dataset"):
        games_by_platform = build_games_for_soundtracks(client, matches)

    assert client.fetched == [10, 11]
    assert sorted(games_by_platform) == [1, 3]
    assert [game.id for game in games_by_platform[3]] == [10]
    assert [game.id for game in games_by_platform[1]] == [11]
    assert games_by_platform[3][0].note == 18  # dict-shaped note is parsed
    assert "jeuInfos game_id=10" in caplog.text


def test_build_games_for_soundtracks_ranks_per_platform_by_scrape_count():
    """Each platform gets its own candidate, ranked by that game's scrapes."""
    client = _StubClient(scrapes={10: 500, 11: 5, 12: 900})
    matches = [
        SoundtrackMatch(
            _soundtrack("/music/1", 10, [1, 3]),
            [
                _candidate(10, 3),  # NES original
                _candidate(11, 1),  # Genesis port
                _candidate(12, 1, title="Mega Man 2 - Remake"),
            ],
        )
    ]

    games_by_platform = build_games_for_soundtracks(client, matches)

    assert [game.id for game in games_by_platform[1]] == [12, 11]
    assert [game.id for game in games_by_platform[3]] == [10]


def test_build_games_for_soundtracks_restricts_to_the_default_platforms():
    """Candidates living only on non-default systems are not fetched."""
    client = _StubClient()
    matches = [
        SoundtrackMatch(
            _soundtrack("/music/1", 10, [1, 20]),
            [_candidate(10, 1), _candidate(11, 20)],  # NES + a system not in defaults
        )
    ]

    games_by_platform = build_games_for_soundtracks(client, matches, platform_ids={1})

    assert client.fetched == [10]  # the platform-20 candidate is skipped
    assert sorted(games_by_platform) == [1]
    assert [game.id for game in games_by_platform[1]] == [10]


def test_build_games_for_soundtracks_without_a_platform_bound_fetches_all():
    client = _StubClient()
    matches = [
        SoundtrackMatch(_soundtrack("/music/1", 10, [1, 20]), [_candidate(10, 1), _candidate(11, 20)])
    ]

    games_by_platform = build_games_for_soundtracks(client, matches)

    assert client.fetched == [10, 11]
    assert sorted(games_by_platform) == [1, 20]


def test_default_platform_ids_only_lists_default_systems():
    systems = [
        _theme_system(1, "megadrive", in_defaults=True),
        _theme_system(4, "snes", in_defaults=True),
        _theme_system(20, "saturn", in_defaults=False),
        _theme_system(None, "unmapped", in_defaults=True),
    ]
    assert build_dataset.default_platform_ids(systems) == {1, 4}


def test_build_games_for_soundtracks_skips_a_failed_game():
    stats = RunStats()
    client = _StubClient(game_errors={10: InvalidResponseError("plain text")})
    matches = [
        SoundtrackMatch(
            _soundtrack("/music/1", 10, [1]), [_candidate(10, 1), _candidate(11, 1)]
        )
    ]

    games_by_platform = build_games_for_soundtracks(client, matches, stats=stats)

    assert [game.id for game in games_by_platform[1]] == [11]
    assert stats.games_without_metadata == 1


def test_build_games_for_soundtracks_aborts_after_consecutive_failures():
    stats = RunStats()
    fails = build_dataset.MAX_CONSECUTIVE_FAILURES
    client = _StubClient(
        game_errors={game_id: InvalidResponseError("plain text") for game_id in range(1, fails + 1)}
    )
    matches = [
        SoundtrackMatch(_soundtrack(f"/music/{game_id}", game_id, [1]), [_candidate(game_id, 1)])
        for game_id in range(1, fails + 2)
    ]

    with pytest.raises(ScreenScraperError):
        build_games_for_soundtracks(client, matches, stats=stats)
    assert stats.games_without_metadata == fails
