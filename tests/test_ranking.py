import pytest

from es_ost.core.models import Game, RankStatus, Track
from es_ost.core.ranking import (
    DEFAULT_CANDIDATE_MIN_SCORE,
    candidate_score,
    candidate_system_ids,
    candidate_titles,
    filter_candidates,
    fuzzy_score,
    highest_scored_track,
    match_game,
    provisional_theme_score,
    rank_games,
    sort_candidates,
    ss_title,
    standard_form,
)


def test_fuzzy_score_is_case_insensitive():
    assert fuzzy_score("earthworm jim", "Earthworm Jim") == 100.0


def test_match_game_prefers_first_exact():
    candidates = [{"name": "Earthworm Jim", "id": 1}, {"name": "Earthworm Jim 2", "id": 2}]
    best, score = match_game("earthworm jim", candidates)
    assert best["id"] == 1
    assert score == 100.0


def test_match_game_rejects_below_threshold():
    candidates = [{"name": "Sonic the Hedgehog", "id": 9}]
    best, _score = match_game("earthworm jim", candidates, threshold=95)
    assert best is None


def test_provisional_theme_score_ordering():
    assert provisional_theme_score("Main Theme") > provisional_theme_score("Opening")
    assert provisional_theme_score("Opening") > provisional_theme_score("Ending")
    assert provisional_theme_score("Ending") == 0.0


def test_rank_games_scrape_count_then_note():
    low_note = Game(id=1, name="a", note=18, scrape_count=100)
    high_note = Game(id=2, name="b", note=19, scrape_count=100)
    low_scrape = Game(id=3, name="c", note=20, scrape_count=50)
    assert [game.id for game in rank_games([low_scrape, low_note, high_note])] == [2, 1, 3]


def test_highest_scored_track_prefers_llm_score():
    keyword_only = Track(rttid="1", title="Opening", theme_score=2.0)
    llm_ranked = Track(rttid="2", title="Main Theme", theme_score=1.0, llm_score=95.0, rank_status=RankStatus.LLM)
    assert highest_scored_track([keyword_only, llm_ranked]).rttid == "2"


def test_candidate_system_ids_union():
    candidate = {"systemeid": "4", "systemes": [{"id": 1}, 2]}
    assert candidate_system_ids(candidate) == {1, 2, 4}


def test_match_game_uses_noms_array():
    candidates = [
        {
            "id": 1,
            "noms": [
                {"region": "ss", "text": "Lemmings 2 - The Tribes"},
                {"region": "us", "text": "Lemmings 2 : The Tribes"},
            ],
        },
        {"id": 2, "noms": [{"region": "ss", "text": "Sonic the Hedgehog"}]},
    ]
    best, score = match_game("Lemmings 2 The Tribes", candidates)
    assert best["id"] == 1
    assert score >= 90.0


def test_candidate_titles_extracts_noms():
    candidate = {"nom": "internal", "noms": [{"region": "ss", "text": "A"}, {"region": "eu", "text": "B"}]}
    assert set(candidate_titles(candidate)) == {"A", "B", "internal"}


def test_candidate_system_ids_from_systeme_dict():
    candidate = {"systeme": {"id": 4, "nom": "Super Nintendo"}}
    assert candidate_system_ids(candidate) == {4}


def test_ss_title_extracts_ss_region():
    candidate = {
        "nom": "internal",
        "noms": [
            {"region": "us", "text": "A-Train US"},
            {"region": "ss", "text": "A-Train"},
        ],
    }
    assert ss_title(candidate) == "A-Train"


def test_sort_candidates_puts_exact_first():
    candidates = [
        {"id": 1, "noms": [{"region": "ss", "text": "A-train Hx"}]},
        {"id": 2, "noms": [{"region": "ss", "text": "A-Train"}]},
    ]
    ordered = sort_candidates("A-Train", candidates)
    assert ordered[0]["id"] == 2


def test_candidate_score_takes_the_best_of_every_title():
    candidate = {
        "nom": "internal",
        "noms": [
            {"region": "ss", "text": "Rock And Roll Racing"},
            {"region": "us", "text": "Rock & Roll Racing"},
        ],
    }
    assert candidate_score("Rock & Roll Racing", candidate) == 100.0
    assert candidate_score("unrelated", candidate) < 100.0


def test_sort_candidates_scores_regional_titles_too():
    """"A candidate matching through a regional title must outrank an ss miss."""
    candidates = [
        {"id": 1, "noms": [{"region": "ss", "text": "Rock And Roll Racing 2"}]},
        {
            "id": 2,
            "noms": [
                {"region": "ss", "text": "Rock And Roll Racing"},
                {"region": "us", "text": "Rock & Roll Racing"},
            ],
        },
    ]
    ordered = sort_candidates("Rock & Roll Racing", candidates)
    assert [candidate["id"] for candidate in ordered] == [2, 1]


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Earthworm Jim: The Video Game", "Earthworm Jim - The Video Game"),
        ("Earthworm Jim : The Video Game", "Earthworm Jim - The Video Game"),
        ("Tetris +", "Tetris Plus"),
        ("Tetris+", "Tetris Plus"),
        ("Taito Chase H.Q. + S.C.I.", "Taito Chase H.Q. Plus S.C.I."),
        ("Rock & Roll Racing", "Rock And Roll Racing"),
        ("Rock & Roll", "Rock And Roll"),
        ("Final Fantasy VII", "Final Fantasy 7"),
        ("Final Fantasy VIII", "Final Fantasy 8"),
        ("Rocky IV", "Rocky 4"),
        ("Final Fantasy XI", "Final Fantasy 11"),
        ("Super Mario Bros. III", "Super Mario Bros. 3"),
        # The article moves to the end, where the `ss` names keep it.
        ("The Killing Game Show", "Killing Game Show, The"),
        ("The 7th Saga", "7th Saga, The"),
        ("The Sims 3: Pets", "Sims 3 - Pets, The"),
        ("The Elder Scrolls IV: Oblivion", "Elder Scrolls 4 - Oblivion, The"),
        # Already in its final position, with or without the comma.
        ("Killing Game Show, The", "Killing Game Show, The"),
        ("Sims 3 - Pets, The", "Sims 3 - Pets, The"),
        ("Goonies The", "Goonies, The"),
    ],
)
def test_standard_form_rewrites_like_ss_names(name, expected):
    assert standard_form(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        "Mega Man X",  # X usually stays a letter in ss names
        "F-Zero X",
        "Final Fantasy X-2",
        "V-Rally",  # a leading numeral is left alone
        "XXL",  # not a standalone numeral
        "Sonic the Hedgehog",
    ],
)
def test_standard_form_leaves_conservative_cases_alone(name):
    assert standard_form(name) == name


def test_candidate_score_ignores_the_ss_rewrite_between_spellings():
    """A query and a candidate differing only by &/And must still score 100."""
    candidate = {"noms": [{"region": "ss", "text": "Command & Conquer"}]}
    assert candidate_score("Command And Conquer", candidate) == 100.0
    assert candidate_score("Command & Conquer", candidate) == 100.0


def test_candidate_score_does_not_care_where_the_article_sits():
    """Moving "The" is part of `standard_form`, so it must not cost any points."""
    assert candidate_score(
        "The Killing Game Show", {"noms": [{"region": "ss", "text": "Killing Game Show, The"}]}
    ) == 100.0
    assert candidate_score(
        "The Sims 3: Pets", {"noms": [{"region": "ss", "text": "Sims 3 - Pets, The"}]}
    ) == 100.0
    # The regional spelling keeps the article in front: same score.
    assert candidate_score(
        "The Killing Game Show", {"noms": [{"region": "us", "text": "The Killing Game Show"}]}
    ) == 100.0


def test_filter_candidates_drops_unrelated_results():
    candidates = [
        {"id": 1, "noms": [{"region": "ss", "text": "Command & Conquer"}]},
        {"id": 2, "noms": [{"region": "ss", "text": "Sonic the Hedgehog"}]},
    ]
    kept = filter_candidates("Command & Conquer", candidates)

    assert [candidate["id"] for candidate in kept] == [1]
    assert DEFAULT_CANDIDATE_MIN_SCORE == 85.0


def test_filter_candidates_drops_the_series_tail_when_the_base_game_exists():
    """The whole series is returned for a first-of-series query; keep the base."""
    candidates = [
        {"id": 1, "noms": [{"region": "ss", "text": "Command & Conquer - Red Alert 3"}]},
        {"id": 2, "noms": [{"region": "ss", "text": "Command & Conquer"}]},
        {"id": 3, "noms": [{"region": "ss", "text": "Command & Conquer - Generals"}]},
    ]
    kept = filter_candidates("Command & Conquer", candidates)

    assert [candidate["id"] for candidate in kept] == [2]


def test_filter_candidates_keeps_subtitled_names_when_no_exact_match_exists():
    """The ss database often only has the subtitled name — do not empty the pool."""
    candidates = [
        {
            "id": 1,
            "noms": [{"region": "ss", "text": "Ace Attorney Investigations - Miles Edgeworth"}],
        },
        {
            "id": 2,
            "noms": [{"region": "ss", "text": "Ace Attorney Investigations - Prosecutor's Path"}],
        },
    ]
    kept = filter_candidates("Ace Attorney Investigations", candidates)

    assert [candidate["id"] for candidate in kept] == [1, 2]


def test_filter_candidates_honours_the_absolute_floor():
    candidates = [
        {"id": 1, "noms": [{"region": "ss", "text": "Command & Conquer - Red Alert 3"}]},
        {"id": 2, "noms": [{"region": "ss", "text": "Chuck Rock"}]},
    ]
    assert filter_candidates("Command & Conquer", candidates) == [candidates[0]]
    assert filter_candidates("Command & Conquer", candidates, min_score=95) == []
    assert filter_candidates("Command & Conquer", []) == []


def test_sort_candidates_agrees_with_match_game():
    """The ordering shortcut must not disagree with the scoring used to pick a winner."""
    candidates = [
        {"id": 1, "noms": [{"region": "ss", "text": "Lemmings 2"}]},
        {
            "id": 2,
            "noms": [
                {"region": "ss", "text": "Lemmings"},
                {"region": "eu", "text": "Lemmings 2 : The Tribes"},
            ],
        },
    ]
    ordered = sort_candidates("Lemmings 2 : The Tribes", candidates)
    best, score = match_game("Lemmings 2 : The Tribes", candidates)

    assert best is not None
    assert best["id"] == ordered[0]["id"] == 2
    assert score == 100.0
