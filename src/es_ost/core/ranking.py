"""Matching and ranking algorithms.

Fuzzy matching uses RapidFuzz when available and falls back to stdlib difflib,
so this module stays importable before dependencies are installed.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any, Iterable

from .models import Game, Track

logger = logging.getLogger(__name__)

# Provisional theme-song keywords, most specific first.
KEYWORDS = ("main theme", "title", "opening", "overture", "theme")

DEFAULT_FUZZY_THRESHOLD = 95.0

#: Candidates scoring below this are dropped before matching (see
#: :func:`filter_candidates`). Comfortably below the acceptance threshold: this
#: narrows the pool, it never decides the match on its own.
DEFAULT_CANDIDATE_MIN_SCORE = 85.0

#: How far below the best candidate a rival may score and still be kept. Needed
#: because ``WRatio`` gives a title that merely *contains* the query as a prefix
#: exactly 90.0, so an absolute cut-off cannot separate "Command & Conquer -
#: Red Alert 3" (90.0) from a real variant of an exact match (95+).
DEFAULT_CANDIDATE_SCORE_BAND = 5.0

#: Roman numerals that ScreenScraper's standardized names spell out in digits.
#: ``X`` is deliberately absent: the ``ss`` names keep it as a letter for titles
#: such as "Mega Man X" and "F-Zero X". A leading token is never converted
#: either, so "V-Rally" survives — see :data:`_ROMAN_PATTERN`.
_ROMAN_NUMERALS: dict[str, int] = {
    "I": 1,
    "II": 2,
    "III": 3,
    "IV": 4,
    "V": 5,
    "VI": 6,
    "VII": 7,
    "VIII": 8,
    "IX": 9,
    "XI": 11,
    "XII": 12,
    "XIII": 13,
    "XIV": 14,
    "XV": 15,
    "XVI": 16,
    "XVII": 17,
    "XVIII": 18,
    "XIX": 19,
    "XX": 20,
}

# A numeral only counts when a space precedes it and a non-word character, a
# space or the end of the name follows it, which keeps "XXL" and a leading
# "V-Rally" untouched.
_ROMAN_PATTERN = re.compile(
    r"(?<=\s)(?P<roman>"
    + "|".join(sorted(_ROMAN_NUMERALS, key=len, reverse=True))
    + r")(?=\s|[^\w\s]|$)"
)


def _roman_to_digit(match: re.Match[str]) -> str:
    return str(_ROMAN_NUMERALS[match.group("roman")])


#: The article moves: ScreenScraper files "The Killing Game Show" as "Killing
#: Game Show, The" and "The Sims 3: Pets" as "Sims 3 - Pets, The". A name can
#: therefore arrive with the article in front, at the end (with or without the
#: comma), or — when the title has a subtitle — right before the dash
#: ("Sims 3, The - Pets"). Only the leading and trailing cases are relocated
#: here; the pre-subtitle spelling is a search variant, because relocating a
#: mid-name "the" would mangle "Sonic the Hedgehog".
_LEADING_ARTICLE = re.compile(r"^(?P<article>the)\s+(?P<rest>\S.*)$", re.IGNORECASE)
_TRAILING_ARTICLE = re.compile(r",\s*the$", re.IGNORECASE)
_BARE_TRAILING_ARTICLE = re.compile(r"(?<=[^,\s])\s+the$", re.IGNORECASE)


def _move_article_to_end(form: str) -> str:
    """Put a leading (or bare trailing) article where the ``ss`` names keep it."""
    if _TRAILING_ARTICLE.search(form):
        return form
    trailing = _BARE_TRAILING_ARTICLE.search(form)
    if trailing:
        return f"{form[: trailing.start()]}, {trailing.group(0).strip()}"
    leading = _LEADING_ARTICLE.match(form)
    if leading:
        return f"{leading.group('rest')}, {leading.group('article')}"
    return form


def standard_form(name: str) -> str:
    """Rewrite ``name`` the way ScreenScraper's standardized (``ss``) names do.

    The ``ss`` titles are not a copy of the submitted ones: a colon becomes a
    dash, ``&`` becomes ``And``, ``+`` becomes ``Plus``, most roman numerals
    become digits, and a leading "The" moves to the end of the name ("Killing
    Game Show, The"). The prep layer builds its search queries from this form,
    and :func:`candidate_score` applies it to both sides of the comparison so
    that "Command & Conquer" still matches "Command And Conquer", and "The
    Killing Game Show" matches the ``ss`` spelling of itself.

    Because the article always lands in the same place, the position it had in
    the input stops mattering to the score. Two rules stay conservative, since
    the ``ss`` names keep the character in these cases: ``X`` is never converted
    ("Mega Man X", "F-Zero X"), and a leading numeral stays put ("V-Rally").
    """
    form = re.sub(r"\s*:\s*", " - ", name.strip())
    form = re.sub(r"\s*\+\s*", " Plus ", form)
    form = re.sub(r"\s*&\s*", " And ", form)
    form = re.sub(r"′", "'", form)
    form = _ROMAN_PATTERN.sub(_roman_to_digit, form)
    return _move_article_to_end(" ".join(form.split()))


def fuzzy_score(query: str, candidate: str) -> float:
    """Return a 0..100 similarity between two strings (case-insensitive)."""
    if not query or not candidate:
        return 0.0
    try:
        from rapidfuzz import fuzz  # type: ignore[import-not-found]

        return float(fuzz.WRatio(query, candidate, processor=str.casefold))
    except ImportError:
        from difflib import SequenceMatcher

        return SequenceMatcher(None, query.casefold(), candidate.casefold()).ratio() * 100.0


def candidate_titles(candidate: dict[str, Any]) -> list[str]:
    """Extract every title string for a ScreenScraper ``jeu`` candidate.

    The JSON API stores ``noms`` as an array of ``{"region": ..., "text": ...}``
    dicts; the XML ``nom_xx`` form and the top-level ``nom`` are also handled
    defensively.
    """
    titles: list[str] = []
    noms = candidate.get("noms")
    if isinstance(noms, list):
        for item in noms:
            if isinstance(item, dict):
                text = item.get("text") or item.get("nom")
                if text:
                    titles.append(str(text))
            elif isinstance(item, str) and item:
                titles.append(item)
    elif isinstance(noms, dict):
        for value in noms.values():
            if isinstance(value, str) and value:
                titles.append(value)

    for key in ("nom", "name", "title"):
        value = candidate.get(key)
        if isinstance(value, str) and value:
            titles.append(value)
    return titles


def ss_title(candidate: dict[str, Any]) -> str | None:
    """Return a jeu's standardized (region ``ss``) title, if present.

    Falls back to the top-level ``nom`` (internal) name.
    """
    noms = candidate.get("noms")
    if isinstance(noms, list):
        for item in noms:
            if isinstance(item, dict) and item.get("region") == "ss":
                text = item.get("text") or item.get("nom")
                if text:
                    return str(text)
    nom = candidate.get("nom")
    if isinstance(nom, str) and nom:
        return nom
    return None


def candidate_score(title: str, candidate: dict[str, Any]) -> float:
    """Best 0..100 similarity between ``title`` and any title of ``candidate``.

    Every ``noms`` spelling is scored — the standardized ``ss`` name as well as
    the regional and internal ones — and the best result wins. Ordering and
    matching must share this single notion of score: scoring the ordering on the
    ``ss`` name alone would push a candidate that spells the query exactly in a
    regional title below one that only matches through its standardized name.

    Both sides are compared in :func:`standard_form`, so a query and a candidate
    that differ only by ``&``/``And``, ``+``/``Plus``, a colon or a roman numeral
    still score 100 instead of losing points to the rewrite.
    """
    query = standard_form(title)
    return max(
        (fuzzy_score(query, standard_form(name)) for name in candidate_titles(candidate)),
        default=0.0,
    )


def filter_candidates(
    title: str,
    candidates: list[dict[str, Any]],
    *,
    min_score: float = DEFAULT_CANDIDATE_MIN_SCORE,
    band: float = DEFAULT_CANDIDATE_SCORE_BAND,
) -> list[dict[str, Any]]:
    """Drop candidates that cannot plausibly be ``title``.

    Searching for the first game of a series ("Command & Conquer") returns the
    whole series ("Command & Conquer - Red Alert 3", "... - Generals"). Those
    entries never win the match, but they widen the eligible-platform set the
    assignment step derives from, so they are dropped while the API order of the
    survivors is preserved.

    Two cuts apply. ``min_score`` removes unrelated results. The ``band`` then
    keeps only candidates within ``band`` points of the best one, which is what
    separates a series entry from a real match: ``WRatio`` scores a candidate
    that merely extends the query with a subtitle at exactly 90.0, so when the
    exact game is present (100) the series tail falls below the band. When no
    candidate is better than 90 the band is inert and the whole list survives —
    that is the "the ss database only has the subtitled name" case
    :func:`es_ost.prep.screenscraper.query_variants` searches the pre-colon
    prefix for.

    A fuzzy cut-off cannot separate every sequel — "Earthworm Jim" and
    "Earthworm Jim 2" score 95 — but it removes the unrelated tail.
    """
    if not candidates:
        return []

    scored = [(candidate_score(title, candidate), candidate) for candidate in candidates]
    cutoff = max(min_score, max(score for score, _candidate in scored) - band)
    kept = [candidate for score, candidate in scored if score >= cutoff]

    if len(kept) != len(scored):
        logger.debug(
            "ranking: dropped %d of %d candidates below %.0f for %r",
            len(scored) - len(kept),
            len(scored),
            cutoff,
            title,
        )
    return kept


def sort_candidates(title: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort candidates by relevance: best fuzzy score of ``title`` vs their titles.

    The ScreenScraper API's declared "sorted by probability" is unreliable, so
    we impose our own order, using :func:`candidate_score` — the same score
    :func:`match_game` uses to pick a winner.
    """
    return sorted(
        candidates,
        key=lambda candidate: candidate_score(title, candidate),
        reverse=True,
    )


def match_game(
    title: str,
    candidates: list[dict[str, Any]],
    *,
    threshold: float = DEFAULT_FUZZY_THRESHOLD,
) -> tuple[dict[str, Any] | None, float]:
    """Pick the best ScreenScraper candidate for a soundtrack title.

    Candidates are first re-sorted by fuzzy score against their standardized
    (``ss``) titles; the winner is then chosen against every regional title.
    An exact first result wins immediately; otherwise the highest-scoring
    candidate must meet the confidence threshold.
    """
    if not candidates:
        return None, 0.0

    logger.debug("ranking: matching %r against %d candidates", title, len(candidates))
    candidates = sort_candidates(title, candidates)

    def score(candidate: dict[str, Any]) -> float:
        return candidate_score(title, candidate)

    first = candidates[0]
    first_score = score(first)
    if first_score >= 100.0:
        logger.debug("ranking: exact match %r -> %r", title, ss_title(first))
        return first, first_score

    best = max(candidates, key=score)
    best_score = score(best)
    if best_score >= threshold:
        logger.debug("ranking: matched %r -> %r (score=%.1f)", title, ss_title(best), best_score)
        return best, best_score
    logger.debug("ranking: no match for %r (best_score=%.1f)", title, best_score)
    return None, best_score


def candidate_system_ids(candidate: dict[str, Any]) -> set[int]:
    """Extract the ScreenScraper platform ids from a jeuRecherche candidate.

    A ``jeu`` carries its system as a ``systeme`` object (``{"id": ..., ...}``);
    the scalar ``systemeid``/``plateforme`` and list ``systemes`` shapes are
    handled defensively too.
    """

    def _int(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    ids: set[int] = set()

    systeme = candidate.get("systeme")
    if isinstance(systeme, dict):
        parsed = _int(systeme.get("id"))
        if parsed is not None:
            ids.add(parsed)
    else:
        for key in ("systemeid", "systeme", "plateforme"):
            value = candidate.get(key)
            if value is not None and not isinstance(value, dict):
                parsed = _int(value)
                if parsed is not None:
                    ids.add(parsed)

    for key in ("systemes", "systemeids", "plateformes"):
        value = candidate.get(key)
        if isinstance(value, (list, tuple, set)):
            for item in value:
                if isinstance(item, dict):
                    parsed = _int(item.get("id"))
                else:
                    parsed = _int(item)
                if parsed is not None:
                    ids.add(parsed)

    return ids


def rank_games(games: Iterable[Game]) -> list[Game]:
    """Sort games by scrape_count desc, then note desc."""
    return sorted(games, key=lambda game: (-game.scrape_count, -(game.note if game.note is not None else -1)))


def blended_score(game: Game, w1: float = 1.0, w2: float = 1.0) -> float:
    """Optional experimental blended score."""
    return w1 * math.log10(game.scrape_count + 1) + w2 * (game.note if game.note is not None else 0)


def provisional_theme_score(title: str) -> float:
    """Provisional keyword score for a track title.

    Returns 0.0 for non-matching titles; matching titles score higher when an
    earlier (more specific) keyword hits. The first listed track remains the
    fallback in the dataset builder.
    """
    lowered = title.lower()
    for index, keyword in enumerate(KEYWORDS):
        if keyword in lowered:
            return 1.0 + (len(KEYWORDS) - index) * 0.1
    return 0.0


def effective_score(track: Track) -> float:
    """The score used for track choice: llm_score preferred, theme_score fallback."""
    return track.llm_score if track.llm_score is not None else track.theme_score


def highest_scored_track(tracks: Iterable[Track]) -> Track | None:
    """Return the best track of a soundtrack, or None when empty."""
    return max(tracks, key=effective_score, default=None)
