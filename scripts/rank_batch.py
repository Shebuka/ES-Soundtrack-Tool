"""Helper for the post-CLI LLM ranking pass.

A ranking batch used to be a throwaway snippet; this makes it repeatable:

- ``dump`` prints the next N not-yet-ranked soundtracks from the queue, with the
  context a ranker needs (game name, platforms, track ids, the provisional
  keyword score) and a ``[floor]`` marker on tracks a structural rule already
  rules out. Soundtracks of an already-ranked series are annotated with the
  pick made earlier, so sibling albums stay consistent.
- ``expand`` turns a compact judgement file into the ``rankings.json`` the merge
  expects, validating every rttid and score before anything is written.
- ``mismatches`` reports the ranked soundtracks whose matched game is a
  *different* game, for the manual ``game_id`` pass. It compares through
  :func:`es_ost.core.ranking.standard_form`, so a ScreenScraper ``ss`` spelling
  ("After Burner 2" for "After Burner II") is not reported as a mismatch.

The judgement file is deliberately small. Only the *maximum* score of a
soundtrack is ever used (``ranking.highest_scored_track``), so scoring the
losers precisely buys nothing: name the plausible candidates, let every other
track fall to the floor. Format::

    {
      "/music/510": {"pick": "why this track is the theme",
                     "scores": {"14684": 62, "14670": 58}}
    }

Usage::

    .venv/bin/python scripts/rank_batch.py dump --count 50 > rank/batch02_prompt.txt
    .venv/bin/python scripts/rank_batch.py expand \\
        --judgements rank/batch02_scores.json --rankings rank/rankings.json
    .venv/bin/python -m es_ost.prep.build_dataset merge-rankings \\
        --data-dir data --rankings rank/rankings.json
    .venv/bin/python scripts/rank_batch.py mismatches
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any

FLOOR = 5.0
"""Score of a track nobody judged. Low enough that it can never win, non-zero
so the merge can mark it ``llm`` and it stops being re-queued."""

SHORT_TRACK_SECONDS = 30
"""Anything shorter is a sting, not a loop."""

#: Titles that are cues rather than music. Boss themes and level music are
#: deliberately absent — they are often the best available theme.
NON_THEME_TITLE = re.compile(
    r"\b("
    r"game ?over|stage clear|level clear|round clear|area clear|"
    r"1-?up|credit|credits?|continue|sound test|unused|name entry|"
    r"high ?score|loading|sfx|effects?|logo|pause|jingle"
    r")\b",
    re.IGNORECASE,
)

_SERIES_NOISE = {"the", "a", "an", "of", "and", "vs"}


def duration_seconds(value: str | None) -> int | None:
    """Parse ``mm:ss`` (or ``hh:mm:ss``) into seconds; ``None`` when unparseable."""
    if not value:
        return None
    parts = value.split(":")
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        return None
    seconds = 0
    for number in numbers:
        seconds = seconds * 60 + number
    return seconds


def is_structurally_floored(track: dict[str, Any]) -> bool:
    """A track that cannot be a theme loop, judged without listening to it.

    Two cheap rules: it is too short to loop, or its title names a cue rather
    than a piece of music. This is a *floor*, not a ranking decision — an
    explicit score always wins over it.
    """
    seconds = duration_seconds(track.get("duration"))
    if seconds is not None and seconds < SHORT_TRACK_SECONDS:
        return True
    return bool(NON_THEME_TITLE.search(track.get("title", "")))


def pending_entries(
    queue: list[dict[str, Any]], ranked_ids: set[str]
) -> list[dict[str, Any]]:
    """Queue entries that still hold at least one unranked track, in queue order.

    Selection is by ``rankings.json``-independent state — the dataset's
    ``llm`` tracks — so the slice stays correct after a rebuild renumbers the
    queue.
    """
    return [
        entry
        for entry in queue
        if any(track["rttid"] not in ranked_ids for track in entry["tracks"])
    ]


def series_key(title: str) -> str:
    """First two meaningful tokens of a soundtrack title, for sibling detection.

    A single token over-groups: ``super`` lumps in Super Mario with Super Metroid
    and ``mega`` lumps in Mega Man with Mega Turrican, so every ``Super *`` album
    printed sixteen unrelated hints. Two tokens keep real series together
    ("super mario", "streets of rage") and everything else apart.
    """
    tokens = [
        token
        for token in re.split(r"[^0-9a-z]+", title.lower())
        if token and token not in _SERIES_NOISE
    ]
    return " ".join(tokens[:2])


def build_rankings(
    entries: list[dict[str, Any]], judgements: dict[str, Any]
) -> list[dict[str, Any]]:
    """Expand ``{soundtrack_url: {pick, scores}}`` into merge-ready ranking items.

    Unjudged and structurally-floored tracks get :data:`FLOOR`. Raises
    ``ValueError`` listing every problem (unknown soundtrack, unknown rttid,
    score out of range) so a typo cannot silently rank the wrong track.
    """
    by_url = {entry["soundtrack_url"]: entry for entry in entries}
    errors: list[str] = []
    unknown = [url for url in judgements if url not in by_url]
    if unknown:
        errors.append(f"soundtrack urls not in the queue: {unknown}")

    rankings: list[dict[str, Any]] = []
    for url, judged in judgements.items():
        entry = by_url.get(url)
        if entry is None:
            continue
        scores = {str(rttid): float(score) for rttid, score in judged.get("scores", {}).items()}
        track_ids = [track["rttid"] for track in entry["tracks"]]
        bogus = sorted(set(scores) - set(track_ids))
        if bogus:
            errors.append(f"{url}: rttids not in the queue entry: {bogus}")
            continue
        for rttid, score in scores.items():
            if not 0.0 <= score <= 100.0:
                errors.append(f"{url}: score for {rttid} out of range: {score}")
        if not scores:
            errors.append(f"{url}: no scored track — every track would be floored")
        ordered = sorted(
            track_ids, key=lambda rttid: (-scores.get(rttid, FLOOR), track_ids.index(rttid))
        )
        for position, rttid in enumerate(ordered, start=1):
            item: dict[str, Any] = {
                "rttid": rttid,
                "llm_score": scores.get(rttid, FLOOR),
                "rank": position,
            }
            if position == 1:
                item["reason"] = judged.get("pick", "")
            rankings.append(item)

    if errors:
        raise ValueError("\n".join(errors))
    return rankings


def _ranked_ids(data_dir: Path) -> set[str]:
    """rttids that already carry an authoritative score, per the dataset."""
    from es_ost.core.dataset import load_dataset
    from es_ost.core.models import RankStatus

    if not (data_dir / "version.json").exists():
        return set()
    dataset = load_dataset(data_dir)
    return {
        track.rttid
        for soundtrack in dataset.soundtracks
        for track in soundtrack.tracks
        if track.rank_status is RankStatus.LLM
    }


def _series_hints(
    data_dir: Path, keys: set[str]
) -> dict[str, list[tuple[str, str, float]]]:
    """For each series key, the picks already made for sibling soundtracks."""
    from es_ost.core.dataset import load_dataset
    from es_ost.core.models import RankStatus
    from es_ost.core.ranking import effective_score

    if not (data_dir / "version.json").exists():
        return {}
    dataset = load_dataset(data_dir)
    hints: dict[str, list[tuple[str, str, float]]] = {}
    for soundtrack in dataset.soundtracks:
        key = series_key(soundtrack.title)
        if key not in keys:
            continue
        ranked = [track for track in soundtrack.tracks if track.rank_status is RankStatus.LLM]
        if not ranked or len(ranked) != len(soundtrack.tracks):
            continue
        top = max(ranked, key=effective_score)
        hints.setdefault(key, []).append(
            (soundtrack.title, top.title, float(effective_score(top)))
        )
    return hints


def take_batch(
    entries: list[dict[str, Any]], start: int, count: int, max_tracks: int
) -> list[dict[str, Any]]:
    """Slice the pending entries, stopping early on either limit.

    ``count`` bounds the album count, ``max_tracks`` the context: one 400-track
    album costs more to read than ten 20-track ones, so a track budget keeps
    every batch readable in a few passes.
    """
    batch: list[dict[str, Any]] = []
    tracks = 0
    for entry in entries[start : start + count]:
        if batch and tracks + len(entry["tracks"]) > max_tracks:
            break
        batch.append(entry)
        tracks += len(entry["tracks"])
    return batch


#: Below this similarity a title and its matched game name describe different
#: games. :func:`es_ost.core.ranking.standard_form` already erases the ``ss``
#: rewrites, so what survives at 97+ is punctuation and transliteration noise
#: ("3-D" vs "3D", "Ju Ōken no Nazo" vs "Ju Ouken No Nazo"). A mere prefix
#: extension scores exactly 90 and a wrong sequel 95, so real errors stay below.
MISMATCH_SIMILARITY = 97.0


#: Words ScreenScraper appends to a release name that carries no identity: its
#: ``ss`` names say "Pokémon - Crystal Version" for "Pokémon Crystal". Only a
#: trailing occurrence is dropped, so a real "Special Edition" stays distinct.
_TRAILING_NOISE = frozenset({"version", "edition"})


def _name_tokens(name: str) -> list[str]:
    """``standard_form``'d name folded to alphanumeric tokens, release noise gone."""
    from es_ost.core.ranking import standard_form

    # ScreenScraper escapes `&` as ``&#x26;`` in some names ("Arcanum - Of
    # Steamworks &#x26; Magick Obscura"); unescape before the rewrite so the
    # entity does not survive as the stray token "x26".
    tokens = [
        token
        for token in re.split(r"[^0-9a-z]+", standard_form(html.unescape(name)).casefold())
        if token and token != "the"
    ]
    while len(tokens) > 1 and tokens[-1] in _TRAILING_NOISE:
        tokens.pop()
    return tokens


def title_key(name: str) -> str:
    """Comparison key for a name, blind to ScreenScraper's naming conventions.

    ``standard_form`` rewrites a title the way the ``ss`` names do (``:`` → `` - ``,
    ``&`` → ``And``, ``+`` → ``Plus``, roman numerals → digits); folding the
    remainder to alphanumerics absorbs the punctuation the ``ss`` names still
    carry. The article is dropped because ScreenScraper moves it: the ``ss``
    name of "The Legend of Zelda: Link's Awakening" is "Legend of Zelda, The -
    Link's Awakening", where the article sits mid-name.
    """
    return "".join(_name_tokens(name))


#: ScreenScraper clips a `noms` entry at 50 characters, cutting a long subtitle
#: mid-word: "Battle Squadron : The Destruction of the Barrax Em".
_SS_NAME_LIMIT = 50

_NUMBER_RUN = re.compile(r"\d+")


def _number_runs(name: str) -> list[str]:
    """The digit groups of a name, which is how a sequel number shows up."""
    from es_ost.core.ranking import standard_form

    return sorted(_NUMBER_RUN.findall(standard_form(name)))


def same_title(title: str, game_name: str) -> bool:
    """Whether ``game_name`` is just another spelling of ``title``.

    Four ways for two names to describe the same game without matching textually:
    equal :func:`title_key` (the ``ss`` rewrites plus punctuation), the same words
    in another order ("Viento, El", "Legend of Zelda, The - ..."), a name
    truncated at ScreenScraper's 50-character limit, or a similarity at the
    transliteration end of the scale.

    A different sequel number is never "just a spelling": "Super Mario Bros. 3"
    scores 98 against "Super Mario Bros. 35", so the digit groups must agree
    before any of the fuzzy escapes apply.
    """
    from es_ost.core.ranking import fuzzy_score, standard_form

    if title_key(title) == title_key(game_name):
        return True
    if _number_runs(title) != _number_runs(game_name):
        return False
    if sorted(_name_tokens(title)) == sorted(_name_tokens(game_name)):
        return True
    if len(game_name.strip()) >= _SS_NAME_LIMIT and title_key(title).startswith(
        title_key(game_name)
    ):
        return True
    return fuzzy_score(standard_form(title), standard_form(game_name)) >= MISMATCH_SIMILARITY


def _cached_game_titles(data_dir: Path) -> dict[int, list[str]]:
    """Every title ScreenScraper holds for each game, from the response cache.

    The dataset keeps only the standardized name, but ``noms`` carries the
    regional ones: ScreenScraper files Star Fox as "Starwing" in Europe and
    River City Ransom as "Street Gangs", so a report that compares against the
    standardized name alone calls a correct match a mismatch.

    Both cached endpoints carry ``noms``. ``jeuInfos`` covers the candidates of
    the default systems (the only games ``games_by_platform.json`` holds);
    ``jeuRecherche`` covers every search result, which is what lets a soundtrack
    matched to a non-default platform still be checked at all.
    """
    titles: dict[int, list[str]] = {}
    cache = data_dir / ".cache"
    if not cache.is_dir():
        return titles
    for path in cache.rglob("*"):
        if not path.is_file():
            continue
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        response = body.get("response") or {}
        url = str((body.get("header") or {}).get("commandRequested") or "")
        if "/jeuInfos" in url:
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            games: list[tuple[Any, list[Any]]] = [
                ((query.get("gameid") or [""])[0], [response.get("jeu") or {}])
            ]
        elif "/jeuRecherche" in url:
            games = [(jeu.get("id"), [jeu]) for jeu in response.get("jeux") or [] if jeu]
        else:
            continue
        for raw_id, jeux in games:
            try:
                game_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            known = titles.setdefault(game_id, [])
            for jeu in jeux:
                for nom in jeu.get("noms") or []:
                    text = str(nom.get("text") or "") if isinstance(nom, dict) else ""
                    if text and text not in known:
                        known.append(text)
    return titles


def mismatch_rows(
    data_dir: Path,
) -> tuple[int, list[tuple[str, str, str, int, str, float]], int, int]:
    """Return ``(ranked, to_fix, recognised, unchecked)`` for the ranked soundtracks.

    ``to_fix`` holds the soundtracks whose matched game is a *different* game,
    ``recognised`` counts the ones that differ only in spelling and were dropped,
    ``unchecked`` the ones no name could be found for (a match whose ScreenScraper
    id never reached the cache, so there is nothing to compare against). Only
    fully ranked soundtracks take part: an unranked album has no pick to show and
    a later pass will cover it anyway.
    """
    from es_ost.core.dataset import load_dataset
    from es_ost.core.models import RankStatus
    from es_ost.core.ranking import effective_score, fuzzy_score, standard_form

    dataset = load_dataset(data_dir)
    names: dict[int, str] = {}
    for games in dataset.games_by_platform.values():
        for game in games:
            names.setdefault(game.id, game.name)
    cached = _cached_game_titles(data_dir)

    to_fix: list[tuple[str, str, str, int, str, float]] = []
    recognised = 0
    unchecked = 0
    ranked = 0
    for soundtrack in dataset.soundtracks:
        if not soundtrack.tracks or any(
            track.rank_status is not RankStatus.LLM for track in soundtrack.tracks
        ):
            continue
        ranked += 1
        game_id = soundtrack.matched_game_id
        if game_id is None:
            continue
        game_name = names.get(game_id)
        candidates = [*([game_name] if game_name else []), *cached.get(game_id, [])]
        if not candidates:
            unchecked += 1
            continue
        if any(same_title(soundtrack.title, name) for name in candidates):
            if title_key(soundtrack.title) != title_key(candidates[0]):
                recognised += 1
            continue
        score = fuzzy_score(standard_form(soundtrack.title), standard_form(candidates[0]))
        top = max(soundtrack.tracks, key=effective_score)
        to_fix.append(
            (soundtrack.url, soundtrack.title, candidates[0], game_id, top.title, score)
        )
    to_fix.sort(key=lambda row: row[1].casefold())
    return ranked, to_fix, recognised, unchecked


ACKNOWLEDGED_FILE = "rank/mismatch_ok.tsv"
"""Rows already reviewed by hand: ``url<TAB>why it is correct``, ``#`` comments."""


def _load_acknowledged(path: Path) -> dict[str, str]:
    """Read the hand-verified pairs, so a reviewed row stops coming back."""
    if not path.is_file():
        return {}
    acknowledged: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        url, _, reason = line.partition("\t")
        acknowledged[url.strip()] = reason.strip()
    return acknowledged


def run_mismatches(args: argparse.Namespace) -> int:
    ranked, to_fix, recognised, unchecked = mismatch_rows(Path(args.data_dir))
    acknowledged = _load_acknowledged(Path(args.ignored))
    verified = [row for row in to_fix if row[0] in acknowledged]
    to_fix = [row for row in to_fix if row[0] not in acknowledged]
    lines = [
        "# Ranked soundtracks: soundtrack title vs matched game",
        "",
        f"{len(to_fix)} of the {ranked} ranked soundtracks are matched to a different game;",
        f"{recognised} more differ only in the way ScreenScraper spells names.",
        f"{unchecked} could not be checked (the matched id has no known title).",
        f"{len(verified)} rows were already confirmed correct by hand and are listed",
        "at the end.",
        "",
        "A soundtrack is **not** listed when its title and the matched game's name",
        "agree after [`ranking.standard_form`](../src/es_ost/core/ranking.py) (colon ->",
        "dash, `&` -> And, `+` -> Plus, roman numerals -> digits), after folding",
        "punctuation, dropping the article and a trailing `Version`/`Edition`, when only",
        "the word order differs, or when any regional title the game is filed under",
        "matches.",
        "",
        "So none of these are reported: `After Burner II` / `After Burner 2`,",
        "`Star Fox` / `Starwing`, `Legend Of Zelda, The - ...`, `Pokémon Crystal` /",
        "`Pokémon - Crystal Version`.",
        "",
        "What is left is a genuinely different game: the `game id` column is what to",
        "replace in `soundtracks.json`. A mere prefix extension scores exactly 90 and a",
        "wrong sequel 95, which is why a sequel matched to its namesake lands here.",
        "",
        "| url | soundtrack title | matched game name | game id | similarity | top pick |",
        "|---|---|---|---|---|---|",
    ]
    for url, title, name, game_id, pick, score in to_fix:
        lines.append(f"| {url} | {title} | {name} | {game_id} | {score:.1f} | {pick} |")
    if verified:
        lines += [
            "",
            "## Confirmed correct",
            "",
            "Listed in the acknowledged file with the reason it is the right game.",
            "",
            "| url | soundtrack title | matched game name | game id | why it is correct |",
            "|---|---|---|---|---|",
        ]
        for url, title, name, game_id, _pick, _score in verified:
            lines.append(f"| {url} | {title} | {name} | {game_id} | {acknowledged[url]} |")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        f"{len(to_fix)} to fix, {len(verified)} confirmed, {recognised} spelling-only, "
        f"{unchecked} unchecked, {ranked} ranked -> {output}"
    )
    for url, title, name, game_id, _pick, score in to_fix:
        print(f"{url:12} {score:5.1f}  {title[:40]:40} -> {name[:40]} (id {game_id})")
    return 0


def run_dump(args: argparse.Namespace) -> int:
    queue = json.loads(Path(args.queue).read_text(encoding="utf-8"))
    ranked = _ranked_ids(Path(args.data_dir))
    entries = pending_entries(queue, ranked)
    batch = take_batch(entries, args.start, args.count, args.max_tracks)
    if not batch:
        print(f"nothing left to rank: {len(entries)} soundtracks pending", file=sys.stderr)
        return 1

    hints = _series_hints(Path(args.data_dir), {series_key(e["soundtrack_title"]) for e in batch})
    total = sum(len(entry["tracks"]) for entry in batch)
    floored = sum(
        1 for entry in batch for track in entry["tracks"] if is_structurally_floored(track)
    )
    print(
        f"# batch: {len(batch)} soundtracks, {total} tracks "
        f"({floored} structurally floored, {total - floored} plausible), "
        f"{len(entries) - args.start - len(batch)} soundtracks left after this batch"
    )
    for index, entry in enumerate(batch, start=1):
        print(
            f"\n### {index}. {entry['soundtrack_url']} | {entry['soundtrack_title']} "
            f"| game: {entry['matched_game_name']} | {', '.join(entry['eligible_platforms'])}"
        )
        for sibling, title, score in hints.get(series_key(entry["soundtrack_title"]), []):
            if sibling != entry["soundtrack_title"]:
                print(f"series: {sibling} -> {title} ({score:.0f})")
        for track in entry["tracks"]:
            marker = " [floor]" if is_structurally_floored(track) else ""
            print(
                f"{track['rttid']} | {track['title']} | {track['duration']} "
                f"| kw={track['theme_score']}{marker}"
            )
    return 0


def run_expand(args: argparse.Namespace) -> int:
    queue = json.loads(Path(args.queue).read_text(encoding="utf-8"))
    judgements = json.loads(Path(args.judgements).read_text(encoding="utf-8"))
    try:
        rankings = build_rankings(queue, judgements)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    Path(args.rankings).write_text(
        json.dumps(rankings, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    floored = sum(1 for item in rankings if item["llm_score"] == FLOOR)
    judged = sum(len(judged["scores"]) for judged in judgements.values())
    print(
        f"wrote {len(rankings)} ranked tracks for {len(judgements)} soundtracks "
        f"({judged} judged, {floored} floored) to {args.rankings}"
    )
    by_url = {entry["soundtrack_url"]: entry for entry in queue}
    print()
    for url, judged_entry in judgements.items():
        entry = by_url[url]
        tracks = {track["rttid"]: track for track in entry["tracks"]}
        best = max(
            (item for item in rankings if item["rttid"] in tracks),
            key=lambda item: item["llm_score"],
        )
        print(
            f"{url:12} {best['llm_score']:5.0f}  {entry['soundtrack_title'][:38]:38} "
            f"-> {tracks[best['rttid']]['title'][:44]}"
        )
    print(
        "\nnext: .venv/bin/python -m es_ost.prep.build_dataset merge-rankings "
        f"--data-dir data --rankings {args.rankings}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rank_batch", description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    dump = subparsers.add_parser("dump", help="print the next unranked soundtracks")
    dump.add_argument("--queue", default="rank/ranking_queue.json")
    dump.add_argument("--data-dir", default="data")
    dump.add_argument("--start", type=int, default=0)
    dump.add_argument("--count", type=int, default=50)
    dump.add_argument(
        "--max-tracks",
        type=int,
        default=1300,
        help="stop the batch before it exceeds this many tracks",
    )
    dump.set_defaults(func=run_dump)

    expand = subparsers.add_parser("expand", help="turn a judgement file into rankings.json")
    expand.add_argument("--queue", default="rank/ranking_queue.json")
    expand.add_argument("--judgements", required=True)
    expand.add_argument("--rankings", default="rank/rankings.json")
    expand.set_defaults(func=run_expand)

    mismatches = subparsers.add_parser(
        "mismatches", help="report ranked soundtracks matched to a different game"
    )
    mismatches.add_argument("--data-dir", default="data")
    mismatches.add_argument("--output", default="rank/game_mismatches.md")
    mismatches.add_argument(
        "--ignored",
        default=ACKNOWLEDGED_FILE,
        help="rows already confirmed by hand (url<TAB>reason)",
    )
    mismatches.set_defaults(func=run_mismatches)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
