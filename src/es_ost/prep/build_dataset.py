"""``es-ost-prep`` — build the embedded dataset and merge LLM rankings.

Two subcommands:

- ``build`` — run the full data-prep pipeline and emit the frozen
  dataset plus ``ranking_queue.json``.
- ``merge-rankings`` — fold the agent-produced ``rankings.json`` back into the
  dataset, preserving every already-ranked score.
- ``clear-cache`` — delete the cached ScreenScraper/Retrotracks responses so the
  next ``build`` re-fetches everything.

Developer credentials are read from the root ``.env`` via the shared
:mod:`es_ost.core.env_loader` (the same loader the GUI uses), so the developer
credentials live in one place. ``SCREENSCRAPER_*`` environment variables or the
matching CLI flags override the ``.env`` values. User-level ``ssid``/``sspassword``
come from ``SCREENSCRAPER_SSID``/``SCREENSCRAPER_SSPASSWORD`` or CLI flags.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from es_ost.core import ranking
from es_ost.core.dataset import Dataset, load_dataset, save_dataset
from es_ost.core.env_loader import load_dev_credentials
from es_ost.core.models import Game, RankStatus, Soundtrack, ThemeSystem, Track
from es_ost.core.systemmap import build_theme_systems, bundled_systems_json, load_raw_mapping
from es_ost.logging_setup import configure_logging
from es_ost.prep.cache import clear_cache
from es_ost.prep.retrotracks import ParsedSoundtrack, ParsedTrack, RetrotracksClient
from es_ost.prep.screenscraper import ScreenScraperClient, ScreenScraperError

logger = logging.getLogger(__name__)

MAX_CONSECUTIVE_FAILURES = 5
"""Transient ScreenScraper failures in a row after which the API counts as down.

Retrying thousands of games against a dead API would take hours and burn the
daily quota, so the build stops instead and is re-run later — the failed
requests are not cached, so nothing is lost.
"""


@dataclass(slots=True)
class RunStats:
    """Items degraded because a transient ScreenScraper failure hit them.

    Degradation is deliberately visible: these counts are logged and printed at
    the end of a build, and make the CLI exit non-zero so the incomplete result
    is not mistaken for a clean run.
    """

    unmatched_soundtracks: int = 0
    games_without_metadata: int = 0

    @property
    def degraded(self) -> int:
        return self.unmatched_soundtracks + self.games_without_metadata

    def __str__(self) -> str:
        return (
            f"{self.unmatched_soundtracks} soundtrack(s) left unmatched, "
            f"{self.games_without_metadata} game(s) without metadata"
        )


def compute_provisional_tracks(parsed: list[ParsedTrack]) -> list[Track]:
    """Convert parsed tracks to model Track with provisional theme_score."""
    tracks = []
    for item in parsed:
        tracks.append(
            Track(
                rttid=item.rttid,
                title=item.title,
                duration=item.duration,
                size_mb=item.size_mb,
                composer=item.composer,
                theme_score=ranking.provisional_theme_score(item.title),
            )
        )
    return tracks


def search_candidates(
    client: ScreenScraperClient,
    parsed: ParsedSoundtrack,
    *,
    min_score: float = ranking.DEFAULT_CANDIDATE_MIN_SCORE,
    stats: RunStats | None = None,
) -> list[dict[str, Any]]:
    """Return the search candidates worth keeping for a soundtrack.

    This is the only I/O of the matching phase. A transient failure degrades to
    "no candidates" so the soundtrack stays unmatched instead of aborting the
    build — its tracks and carried-forward LLM scores stay valid, and the manual
    tab exists for exactly this case. Fatal errors (bad credentials, closed API,
    exhausted quota) are re-raised, because every following lookup would fail
    the same way.

    Candidates below ``min_score`` are discarded, so a search for the first game
    of a series does not inherit the whole series.
    """
    try:
        candidates = client.search_variants(parsed.title)
    except ScreenScraperError as exc:
        if exc.fatal:
            raise
        logger.warning(
            "build: search failed for %r — leaving it unmatched: %s", parsed.title, exc
        )
        if stats is not None:
            stats.unmatched_soundtracks += 1
        return []
    return ranking.filter_candidates(parsed.title, candidates, min_score=min_score)


def match_soundtrack(
    parsed: ParsedSoundtrack,
    candidates: list[dict[str, Any]],
    *,
    threshold: float = ranking.DEFAULT_FUZZY_THRESHOLD,
) -> Soundtrack:
    """Pick the best candidate and record every platform the candidates cover.

    Pure: the search and the metadata fetch happen around this, so the ranking
    can be exercised without a client. ``eligible_screenscraper_ids`` is the
    union of the candidates' platforms — a soundtrack is usable on every system
    one of its plausible matches was released on.

    ``matched_game_id`` is coerced to ``int``: ``jeuRecherche`` returns ids as
    strings, and storing one verbatim made every int-keyed lookup miss — that
    is what silently disabled the ``scrape_count`` tiebreak in ``auto_assign``.
    """
    best, confidence = ranking.match_game(parsed.title, candidates, threshold=threshold)

    eligible: set[int] = set()
    for candidate in candidates:
        eligible |= ranking.candidate_system_ids(candidate)

    matched_game_id = _candidate_game_id(best) if best is not None else None

    return Soundtrack(
        url=f"/music/{parsed.sid}",
        title=parsed.title,
        matched_game_id=matched_game_id,
        match_confidence=confidence,
        eligible_screenscraper_ids=sorted(eligible),
    )


@dataclass(slots=True)
class SoundtrackMatch:
    """A soundtrack plus the search candidates it was matched from.

    The candidates are kept because the per-platform metadata fetch needs them:
    the winner answers "which game is this soundtrack", while the losing
    candidates answer "which game represents this soundtrack *on platform X*".
    """

    soundtrack: Soundtrack
    candidates: list[dict[str, Any]] = field(default_factory=list)


def carry_forward(soundtracks: list[Soundtrack], previous: Dataset | None) -> None:
    """Preserve prior LLM scores; mark new/changed tracks as pending."""
    if previous is None:
        for soundtrack in soundtracks:
            for track in soundtrack.tracks:
                track.rank_status = RankStatus.PENDING
        return

    previous_tracks = {
        track.rttid: track
        for soundtrack in previous.soundtracks
        for track in soundtrack.tracks
    }
    for soundtrack in soundtracks:
        for track in soundtrack.tracks:
            prior = previous_tracks.get(track.rttid)
            if prior is not None and prior.llm_score is not None and prior.rank_status is RankStatus.LLM:
                track.llm_score = prior.llm_score
                track.rank_status = RankStatus.LLM
            else:
                track.rank_status = RankStatus.PENDING


@dataclass(slots=True)
class RankingQueue:
    """The pending-soundtracks manifest for the post-CLI LLM pass.

    Soundtrack-rooted on purpose: choosing a theme song is a comparison *within*
    one soundtrack, so one entry is one LLM call, and the soundtrack context is
    written once instead of being repeated on every track.

    ``skipped_*`` count pending tracks that are deliberately **not** queued
    because their soundtrack has no eligible platform. Auto mode can never reach
    those tracks (:func:`es_ost.core.assign.section1_soundtracks`), so ranking
    them would buy nothing; they keep ``rank_status = pending`` and become
    queueable only if a later build finds search candidates for them.
    """

    entries: list[dict[str, Any]] = field(default_factory=list)
    skipped_soundtracks: int = 0
    skipped_tracks: int = 0

    @property
    def queued_tracks(self) -> int:
        """Tracks actually handed to the LLM pass."""
        return sum(len(entry["tracks"]) for entry in self.entries)

    @property
    def pending_tracks(self) -> int:
        """Every pending track, queued or skipped."""
        return self.queued_tracks + self.skipped_tracks


def _queue_game_name(
    soundtrack: Soundtrack, game_names: Mapping[int, str] | None
) -> str | None:
    """Name of the matched game, or ``None`` when nothing was matched.

    ``game_names`` is keyed by the int ``Game.id`` and ``matched_game_id`` is an
    int too (``match_soundtrack`` and ``serialize.soundtrack_from_dict`` coerce
    it), so the lookup is direct.

    The metadata fetch is bounded to the default platforms, so a soundtrack
    matched to a game that only exists on a non-default system has no entry in
    ``games_by_platform``; the soundtrack title remains available in the queue
    as the best label for such an entry.
    """
    if soundtrack.matched_game_id is None:
        return None
    return (game_names or {}).get(soundtrack.matched_game_id) or soundtrack.title


def _queue_platform_labels(
    platform_ids: list[int], platform_names: Mapping[int, str] | None
) -> list[str]:
    """Human-readable platform names; an unknown id stays as its own string."""
    names = platform_names or {}
    return [names.get(platform_id, str(platform_id)) for platform_id in platform_ids]


def _queue_track(track: Track) -> dict[str, Any]:
    """One pending track, carrying the provisional score as an anchor."""
    return {
        "rttid": track.rttid,
        "title": track.title,
        "duration": track.duration,
        "size_mb": track.size_mb,
        "composer": track.composer,
        "theme_score": track.theme_score,
    }


def build_ranking_queue(
    soundtracks: list[Soundtrack],
    *,
    game_names: Mapping[int, str] | None = None,
    platform_names: Mapping[int, str] | None = None,
) -> RankingQueue:
    """Emit the pending-soundtracks manifest for the post-CLI LLM pass.

    One entry per soundtrack with at least one ``pending`` track, because the
    ranking task is comparative: a lone track called "Theme" is only rankable
    next to its siblings. ``game_names``/``platform_names`` are injected rather
    than fetched, so this stays pure and testable without a client.

    A soundtrack with no eligible ScreenScraper id is skipped — auto mode cannot
    reach it, so its tracks would be ranked for nothing. A soundtrack with a
    ``None`` ``matched_game_id`` but a non-empty eligible set is **kept**: that
    set is the union of every candidate's platforms, and assignment reads only
    that, so "no confident match, but the game exists on these systems" is still
    usable.
    """
    queue = RankingQueue()
    for soundtrack in soundtracks:
        pending = [
            track for track in soundtrack.tracks if track.rank_status is RankStatus.PENDING
        ]
        if not pending:
            continue
        platform_ids = sorted(set(soundtrack.eligible_screenscraper_ids))
        if not platform_ids:
            queue.skipped_soundtracks += 1
            queue.skipped_tracks += len(pending)
            continue
        queue.entries.append(
            {
                "soundtrack_url": soundtrack.url,
                "soundtrack_title": soundtrack.title,
                "matched_game_id": soundtrack.matched_game_id,
                "matched_game_name": _queue_game_name(soundtrack, game_names),
                "eligible_screenscraper_ids": platform_ids,
                "eligible_platforms": _queue_platform_labels(platform_ids, platform_names),
                "tracks": [_queue_track(track) for track in pending],
            }
        )
    if queue.skipped_tracks:
        logger.info(
            "queue: skipped %d pending track(s) in %d soundtrack(s) with no eligible platform",
            queue.skipped_tracks,
            queue.skipped_soundtracks,
        )
    return queue


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _ranking_items(raw: Any) -> list[dict[str, Any]]:
    """Flatten any accepted ``rankings.json`` shape into score-bearing objects.

    Three shapes are accepted, so a ranking run can be written batch by batch in
    whatever grouping it was produced in: a flat list of
    ``{"rttid", "llm_score"}`` objects, a ``{rttid: score}`` map, and a
    soundtrack-rooted shape mirroring ``ranking_queue.json`` (either a
    ``{soundtrack_url: [...]}`` mapping or a list of objects carrying a
    ``tracks`` list). Extra keys such as ``rank`` or ``reason`` are ignored, so
    the file can carry its audit trail. Anything else raises ``ValueError``.
    """
    if isinstance(raw, dict):
        if raw and all(_is_number(value) for value in raw.values()):
            return [{"rttid": key, "llm_score": value} for key, value in raw.items()]
        items: list[dict[str, Any]] = []
        for value in raw.values():
            items.extend(_ranking_items(value))
        return items
    if isinstance(raw, list):
        items = []
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError(f"ranking entry is not an object: {item!r}")
            if "tracks" in item:
                items.extend(_ranking_items(item["tracks"]))
            else:
                items.append(item)
        return items
    raise ValueError(f"unsupported rankings file: {type(raw).__name__}")


def flatten_rankings(raw: Any) -> dict[str, float]:
    """Normalize a ``rankings.json`` payload into ``{rttid: llm_score}``.

    Validation happens before :func:`apply_rankings` touches the dataset, so a
    malformed or out-of-range file aborts the merge instead of leaving it
    half-applied — the dataset is only written after the whole file is valid.
    Duplicate rttids are last-wins, which keeps re-running a batch idempotent.
    """
    rankings: dict[str, float] = {}
    for item in _ranking_items(raw):
        try:
            rttid = str(item["rttid"])
        except (KeyError, TypeError):
            raise ValueError(f"ranking entry has no 'rttid': {item!r}") from None
        if item.get("llm_score") is None:
            raise ValueError(f"ranking entry for rttid {rttid!r} has no 'llm_score'")
        try:
            score = float(item["llm_score"])
        except (TypeError, ValueError):
            raise ValueError(
                f"llm_score for rttid {rttid!r} is not a number: {item['llm_score']!r}"
            ) from None
        if not 0.0 <= score <= 100.0:
            raise ValueError(f"llm_score for rttid {rttid!r} out of range: {score}")
        rankings[rttid] = score
    return rankings


def apply_rankings(soundtracks: list[Soundtrack], rankings: dict[str, float]) -> int:
    """Apply ``{rttid: llm_score}`` to pending tracks; return the number applied."""
    applied = 0
    for soundtrack in soundtracks:
        for track in soundtrack.tracks:
            if track.rttid in rankings:
                score = float(rankings[track.rttid])
                if not 0.0 <= score <= 100.0:
                    raise ValueError(f"llm_score for {track.rttid} out of range: {score}")
                track.llm_score = score
                track.rank_status = RankStatus.LLM
                applied += 1
    return applied


def _scalar(value: Any) -> Any:
    """Unwrap ScreenScraper's ``{"text": "16"}`` field shape into the value."""
    if isinstance(value, dict):
        return value.get("text")
    return value


def _to_int(value: Any) -> int:
    try:
        return int(_scalar(value))
    except (TypeError, ValueError):
        return 0


def _game_from_jeu(game_id: int, jeu: dict[str, Any], name: str | None = None) -> Game:
    """Build a Game from the ``jeu`` object of a jeuInfos response.

    ``note`` is the 0-20 community score and arrives either as ``null`` or as
    ``{"text": "16"}``. The dict shape used to be handed straight to ``int()``,
    which raised ``TypeError`` and was swallowed into ``None`` — that is why
    every ``note`` in the first dataset was null. The scrape count is the
    cumulative ``nbscrap`` across every ``rom`` in ``roms``.

    ``name`` defaults to the standardized ``ss`` title, since the build no longer
    reads a CSV row for every game.
    """
    note_value = _scalar(jeu.get("note"))
    try:
        note = int(note_value) if note_value not in (None, "") else None
    except (TypeError, ValueError):
        note = None

    scrape_count = 0
    roms = jeu.get("roms")
    if isinstance(roms, list):
        for rom in roms:
            if isinstance(rom, dict):
                scrape_count += _to_int(rom.get("nbscrap"))

    title = name or ranking.ss_title(jeu) or str(game_id)
    return Game(id=game_id, name=title, note=note, scrape_count=scrape_count)


#: Preference order for a system's display name; ``systemesListe`` nests them
#: under ``noms`` (``nom_eu``/``nom_us``/``nom_recalbox``/...).
_PLATFORM_NAME_KEYS = ("nom_eu", "nom_us", "nom_wor", "nom_recalbox", "nom_retropie")


def _platforms_from_systems(systems: list[dict[str, Any]]) -> dict[int, str]:
    """Build ``{screenscraper_id: display_name}`` from systemesListe.

    The names live in a nested ``noms`` object; looking for a top-level
    ``nom``/``name`` made every entry fall back to its own id, which turned
    platforms.json into a useless id-to-id map.
    """
    platforms: dict[int, str] = {}
    for system in systems:
        sid = _to_int(system.get("id"))
        if not sid:
            continue
        names = system.get("noms")
        label = ""
        if isinstance(names, dict):
            for key in _PLATFORM_NAME_KEYS:
                value = names.get(key)
                if isinstance(value, str) and value.strip():
                    label = value.strip()
                    break
        if not label:
            label = str(system.get("nom") or system.get("name") or sid)
        platforms[sid] = label
    return platforms


def _candidate_game_id(candidate: dict[str, Any]) -> int | None:
    try:
        return int(candidate["id"])
    except (KeyError, TypeError, ValueError):
        return None


def default_platform_ids(theme_systems: list[ThemeSystem]) -> set[int]:
    """ScreenScraper ids of the systems the tool enables by default."""
    return {
        system.screenscraper_id
        for system in theme_systems
        if system.in_defaults and system.screenscraper_id is not None
    }


def build_games_for_soundtracks(
    client: ScreenScraperClient,
    matches: list[SoundtrackMatch],
    *,
    platform_ids: set[int] | None = None,
    stats: RunStats | None = None,
) -> dict[int, list[Game]]:
    """Fetch ``jeuInfos`` per search *candidate*, then rank each platform.

    The old flow walked every default system's ``gameslist.csv`` and called
    ``jeuInfos`` for all ~24k ids. That is far too many, but fetching only the
    match *winner* is too few: the winner answers "which game is this
    soundtrack", while the per-platform question is "which candidate was
    released on this system". For "Mega Man 2" the search returns the NES
    original, the Genesis port and a remake; the port is what belongs on the
    Genesis theme, with its own scrape count.

    ``platform_ids`` bounds the work to the systems the tool actually uses
    (``in_defaults``): candidates whose platforms are all outside that set are
    skipped, which on the first dataset cuts 4,257 candidate ids down to 1,160
    across the 11 default systems. ``games_by_platform`` ends up holding only
    those platforms; promoting another system to the defaults on a later build
    fetches just the candidates it adds, and the response cache means the
    already-fetched ones cost nothing.

    A candidate whose ``jeuInfos`` call fails after retries is dropped from the
    lists (assignment then sees no metadata for it); ``MAX_CONSECUTIVE_FAILURES``
    failures in a row abort the run, because that pattern means the API is down
    rather than one game being broken.
    """
    wanted: dict[int, set[int]] = {}
    skipped = 0
    for match in matches:
        for candidate in match.candidates:
            game_id = _candidate_game_id(candidate)
            if game_id is None:
                continue
            platforms = ranking.candidate_system_ids(candidate)
            if platform_ids is not None:
                platforms &= platform_ids
            if not platforms:
                skipped += 1
                continue
            wanted.setdefault(game_id, set()).update(platforms)

    game_ids = sorted(wanted)
    logger.info(
        "games: fetching jeuInfos for %d candidate games on %s platforms "
        "(%d soundtracks, %d candidates on other platforms skipped)",
        len(game_ids),
        "all" if platform_ids is None else len(platform_ids),
        len(matches),
        skipped,
    )

    fetched: dict[int, Game] = {}
    consecutive_failures = 0
    progress_every = max(1, len(game_ids) // 10)
    for position, game_id in enumerate(game_ids, start=1):
        logger.debug(
            "games: [%d/%d] jeuInfos game_id=%d", position, len(game_ids), game_id
        )
        try:
            jeu = client.game_info(game_id)
        except ScreenScraperError as exc:
            if exc.fatal:
                raise
            consecutive_failures += 1
            logger.warning(
                "games: jeuInfos failed for game_id=%d — leaving it unranked: %s",
                game_id,
                exc,
            )
            if stats is not None:
                stats.games_without_metadata += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                raise ScreenScraperError(
                    f"{consecutive_failures} consecutive jeuInfos failures — the "
                    "ScreenScraper API looks unavailable; re-run the build later "
                    "(failed requests are not cached)"
                ) from exc
            continue
        consecutive_failures = 0
        fetched[game_id] = _game_from_jeu(game_id, jeu)
        if position % progress_every == 0 or position == len(game_ids):
            logger.info(
                "games: %d/%d candidate games fetched (%.0f%%)",
                position,
                len(game_ids),
                100.0 * position / len(game_ids),
            )

    result: dict[int, list[Game]] = {}
    for platform_id in sorted({pid for pids in wanted.values() for pid in pids}):
        games = [
            fetched[game_id]
            for game_id, platform_ids in wanted.items()
            if platform_id in platform_ids and game_id in fetched
        ]
        if games:
            result[platform_id] = ranking.rank_games(games)

    logger.info(
        "games: %d games with metadata spread over %d platforms",
        len(fetched),
        len(result),
    )
    return result


def _dataset_version() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def run_build(args: argparse.Namespace) -> int:
    """Run the pipeline; returns non-zero when items were degraded (see RunStats)."""
    systems_json = Path(args.systems_json)
    data_dir = Path(args.data_dir)
    rank_dir = Path(args.rank_dir)
    cache_dir = _resolve_cache_dir(args)

    theme_systems = build_theme_systems(load_raw_mapping(systems_json))
    logger.info(
        "build: loaded %d theme systems from %s (%d in defaults)",
        len(theme_systems),
        systems_json,
        sum(1 for system in theme_systems if system.in_defaults),
    )

    client = ScreenScraperClient(
        devid=args.devid,
        devpassword=args.devpassword,
        softname=args.softname,
        ssid=args.ssid,
        sspassword=args.sspassword,
        cache_dir=cache_dir,
    )
    if args.verify_login:
        quota = client.verify_login()
        logger.info("build: ScreenScraper login OK — quota: %s", quota)
        print(f"login OK — quota: {quota}")

    retrotracks = RetrotracksClient(cache_dir=cache_dir)
    parsed_soundtracks = retrotracks.index_soundtracks()
    logger.info(
        "build: found %d soundtracks on Retrotracks index",
        len(parsed_soundtracks),
    )
    stats = RunStats()
    matches: list[SoundtrackMatch] = []
    for position, parsed in enumerate(parsed_soundtracks, start=1):
        logger.info(
            "build: scanning soundtrack %d/%d — %r (sid=%s)",
            position,
            len(parsed_soundtracks),
            parsed.title,
            parsed.sid,
        )
        tracks = compute_provisional_tracks(retrotracks.soundtrack_tracks(parsed.sid))
        candidates = search_candidates(
            client, parsed, min_score=args.min_candidate_score, stats=stats
        )
        matched = match_soundtrack(parsed, candidates, threshold=args.threshold)
        matched.tracks = tracks
        logger.info(
            "build:   %d tracks; best match game_id=%s confidence=%.1f (%d candidates, %d eligible ids)",
            len(tracks),
            matched.matched_game_id,
            matched.match_confidence,
            len(candidates),
            len(matched.eligible_screenscraper_ids),
        )
        matches.append(SoundtrackMatch(matched, candidates))

    soundtracks = [match.soundtrack for match in matches]

    previous = None
    if (data_dir / "version.json").exists():
        previous = load_dataset(data_dir)
        logger.info("build: loaded previous dataset from %s", data_dir)

    carry_forward(soundtracks, previous)
    pending_tracks = sum(
        1
        for soundtrack in soundtracks
        for track in soundtrack.tracks
        if track.rank_status is RankStatus.PENDING
    )
    llm_tracks = sum(
        1
        for soundtrack in soundtracks
        for track in soundtrack.tracks
        if track.rank_status is RankStatus.LLM
    )
    logger.info("build: carried forward scores — %d LLM, %d pending", llm_tracks, pending_tracks)

    games_by_platform = build_games_for_soundtracks(
        client,
        matches,
        platform_ids=default_platform_ids(theme_systems),
        stats=stats,
    )
    platforms = _platforms_from_systems(client.systems())

    dataset = Dataset(
        version=_dataset_version(),
        build_date=datetime.now(timezone.utc).isoformat(),
        platforms=platforms,
        theme_systems=theme_systems,
        soundtracks=soundtracks,
        games_by_platform=games_by_platform,
    )
    save_dataset(dataset, data_dir)
    logger.info("build: saved dataset version %s to %s", dataset.version, data_dir)

    rank_dir.mkdir(parents=True, exist_ok=True)
    # The queue is soundtrack-rooted and carries the *names*, so whoever runs the
    # LLM pass never has to resolve an id against games_by_platform.json. Only the
    # default platforms are fetched, so a game that exists solely on a non-default
    # system falls back to the soundtrack title (_queue_game_name).
    game_names = {
        game.id: game.name for games in games_by_platform.values() for game in games
    }
    queue = build_ranking_queue(
        soundtracks,
        game_names=game_names,
        platform_names=platforms,
    )
    (rank_dir / "ranking_queue.json").write_text(
        json.dumps(queue.entries, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info(
        "build: wrote %d soundtracks; %d of %d pending tracks queued for LLM ranking, "
        "%d skipped (no eligible platform)",
        len(soundtracks),
        queue.queued_tracks,
        queue.pending_tracks,
        queue.skipped_tracks,
    )
    print(
        f"wrote {len(soundtracks)} soundtracks; {queue.queued_tracks} of "
        f"{queue.pending_tracks} pending tracks queued for LLM ranking "
        f"({queue.skipped_tracks} skipped: no eligible platform)"
    )

    if stats.degraded:
        summary = (
            f"{stats} because the ScreenScraper API failed; the failures were not "
            "cached, so re-running the build retries them"
        )
        logger.warning("build: %s", summary)
        print(f"warning: {summary}")
        return 1
    return 0


def _resolve_cache_dir(args: argparse.Namespace) -> Path:
    """Cache location: ``--cache-dir`` if given, else ``<data-dir>/.cache``."""
    return Path(args.cache_dir) if args.cache_dir else Path(args.data_dir) / ".cache"


def run_clear_cache(args: argparse.Namespace) -> None:
    cache_dir = _resolve_cache_dir(args)
    logger.info("clear-cache: removing cached responses from %s", cache_dir)
    result = clear_cache(cache_dir)
    print(f"cleared {cache_dir}: {result}")


def run_merge(args: argparse.Namespace) -> int:
    """Fold ``rankings.json`` into the dataset; returns non-zero on a bad file."""
    data_dir = Path(args.data_dir)
    rankings_path = Path(args.rankings)
    logger.info("merge: loading dataset from %s", data_dir)
    dataset = load_dataset(data_dir)

    logger.info("merge: loading rankings from %s", rankings_path)
    raw = json.loads(rankings_path.read_text(encoding="utf-8"))
    try:
        rankings = flatten_rankings(raw)
    except ValueError as exc:
        logger.error("merge: %s — nothing was written", exc)
        print(f"error: {exc}")
        return 1

    applied = apply_rankings(dataset.soundtracks, rankings)
    save_dataset(dataset, data_dir)
    logger.info("merge: applied %d LLM rankings into %s", applied, data_dir)
    print(f"merged {applied} LLM rankings into {data_dir}")

    unknown = len(rankings) - applied
    if unknown:
        message = f"{unknown} ranked rttid(s) are not in the dataset — stale file?"
        logger.warning("merge: %s", message)
        print(f"warning: {message}")
    return 0


def _credential_default(env_var: str, fallback: str) -> str:
    """Prefer an explicit SCREENSCRAPER_* env var, else a fallback value."""
    return os.environ.get(env_var) or fallback


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="es-ost-prep", description="Build the ES Soundtrack Tool dataset")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="run the data-prep pipeline")
    build.add_argument("--systems-json", default=str(bundled_systems_json()))
    build.add_argument("--data-dir", default="data")
    build.add_argument("--rank-dir", default="rank")
    build.add_argument("--cache-dir", default=None)
    build.add_argument("--threshold", type=float, default=ranking.DEFAULT_FUZZY_THRESHOLD)
    build.add_argument(
        "--min-candidate-score",
        type=float,
        default=ranking.DEFAULT_CANDIDATE_MIN_SCORE,
        help="drop search candidates scoring below this (0-100)",
    )
    build.add_argument("--verify-login", action="store_true")
    dev = load_dev_credentials()
    build.add_argument("--devid", default=_credential_default("SCREENSCRAPER_DEVID", dev.devid))
    build.add_argument("--devpassword", default=_credential_default("SCREENSCRAPER_DEVPASSWORD", dev.devpassword))
    build.add_argument("--softname", default=_credential_default("SCREENSCRAPER_SOFTNAME", dev.softname))
    build.add_argument("--ssid", default=os.environ.get("SCREENSCRAPER_SSID"))
    build.add_argument("--sspassword", default=os.environ.get("SCREENSCRAPER_SSPASSWORD"))
    build.set_defaults(func=run_build)

    merge = subparsers.add_parser("merge-rankings", help="merge rankings.json into the dataset")
    merge.add_argument("--data-dir", default="data")
    merge.add_argument("--rankings", default="rank/rankings.json")
    merge.set_defaults(func=run_merge)

    clear = subparsers.add_parser(
        "clear-cache",
        help="delete cached ScreenScraper/Retrotracks responses",
    )
    clear.add_argument("--data-dir", default="data")
    clear.add_argument("--cache-dir", default=None)
    clear.set_defaults(func=run_clear_cache)

    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    logger.info("es-ost-prep starting (command=%s)", args.command)
    return args.func(args) or 0


if __name__ == "__main__":
    main()
