import json

import pytest

from es_ost.core.dataset import Dataset, load_dataset, save_dataset
from es_ost.core.models import (
    Assignment,
    Game,
    Project,
    Provenance,
    RankStatus,
    Soundtrack,
    ThemeSystem,
    Track,
)
from es_ost.core.project import load_project, save_project
from es_ost.core.serialize import soundtrack_from_dict


def test_soundtrack_from_dict_coerces_the_matched_game_id():
    """jeuRecherche sends the id as a string; the dataset holds it as an int.

    A verbatim string made every int-keyed lookup miss — most visibly the
    ``scrape_count`` tiebreak in ``auto_assign`` and the game-name resolution of
    ``build_ranking_queue``.
    """
    soundtrack = soundtrack_from_dict(
        {
            "url": "/music/1",
            "title": "1944: The Loop Master",
            "matched_game_id": "37385",
            "match_confidence": 100.0,
            "eligible_screenscraper_ids": ["7"],
            "tracks": [],
        }
    )

    assert soundtrack.matched_game_id == 37385
    assert isinstance(soundtrack.matched_game_id, int)


def test_soundtrack_from_dict_keeps_an_absent_matched_game_id_null():
    soundtrack = soundtrack_from_dict({"url": "/music/1", "title": "X", "tracks": []})
    assert soundtrack.matched_game_id is None


def test_dataset_roundtrip(tmp_path):
    dataset = Dataset(
        version="v1",
        build_date="2026-01-01",
        platforms={4: "Super Nintendo"},
        theme_systems=[ThemeSystem(theme_name="snes", rom_folder="snes", screenscraper_id=4)],
        soundtracks=[
            Soundtrack(
                url="/music/1",
                title="Earthworm Jim",
                tracks=[
                    Track(
                        rttid="rtt-1",
                        title="Main Theme",
                        theme_score=1.5,
                        llm_score=90.0,
                        rank_status=RankStatus.LLM,
                    )
                ],
            )
        ],
        games_by_platform={4: [Game(id=1, name="Earthworm Jim", note=18, scrape_count=500)]},
    )
    save_dataset(dataset, tmp_path)
    loaded = load_dataset(tmp_path)

    assert loaded.platforms == {4: "Super Nintendo"}
    assert loaded.soundtracks[0].tracks[0].llm_score == 90.0
    assert loaded.games_by_platform[4][0].note == 18


def test_project_roundtrip(tmp_path):
    project = Project(
        name="My Project",
        systems=[ThemeSystem(theme_name="snes", rom_folder="snes", screenscraper_id=4)],
        assignments=[
            Assignment(theme_name="snes", soundtrack_url="/music/1", track_rttid="rtt-1", provenance=Provenance.MANUAL)
        ],
    )
    save_project(project, tmp_path / "project.json")
    loaded = load_project(tmp_path / "project.json")

    assert loaded.name == "My Project"
    assert loaded.assignments[0].provenance is Provenance.MANUAL


def test_project_roundtrip_preserves_locked_assignments(tmp_path):
    project = Project(
        systems=[ThemeSystem(theme_name="snes", rom_folder="snes", screenscraper_id=4)],
        assignments=[
            Assignment(
                theme_name="snes",
                soundtrack_url="/music/1",
                track_rttid="rtt-1",
                provenance=Provenance.MANUAL,
                locked=True,
            )
        ],
    )
    save_project(project, tmp_path / "project.json")
    loaded = load_project(tmp_path / "project.json")

    assert loaded.assignments[0].locked is True


def test_project_rejects_unsupported_schema(tmp_path):
    (tmp_path / "project.json").write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_project(tmp_path / "project.json")
