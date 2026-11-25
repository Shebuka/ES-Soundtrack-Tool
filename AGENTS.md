# AGENTS.md

This file is the developer/agent reference for the repository: the invariants
that are easy to break, the non-obvious behaviour of the upstream APIs and the
way data flows from the scrapers into the shipped app. The user-facing guide is
[`README.md`](README.md).

## Repository state

- Python 3.12 + PySide6 application in [`src/es_ost/`](src/es_ost), `src/`
  layout, two entry points: `es-ost-prep` (headless CLI) and
  `es-soundtrack-tool` (GUI).
- Install/test: `pip install -e ".[dev]"` then `.venv/bin/python -m pytest`.
- A normal checkout already contains the frozen dataset and the LLM rankings,
  so the GUI works offline without scraping.

## Layering

- `core/` — pure domain logic: no network and no Qt.
- `prep/` — headless CLI + scrapers (network; imports `core`).
- `gui/` — Qt only (imports `core` and `prep`).
- [`downloader.py`](src/es_ost/downloader.py) — on-demand MP3 fetch + ffmpeg transcode.

## Frozen builds and releases

- [`main.py`](main.py) is the pyside6-deploy/Nuitka entry point. Nuitka reads
  `# nuitka-project` directives only from the compiled main script, so the
  `data`/`assets` includes, the scrape-cache exclusion and the Windows metadata
  live there, not in `app.py`. `systems.json` rides along in `data/`, so there
  is no package-data include.
- [`app.py`](src/es_ost/gui/app.py) imports `rc_data` statically
  (`try: from . import rc_data`). A dynamic import is invisible to Nuitka and
  silently drops the embedded `.env` from a frozen build.
- [`paths.py`](src/es_ost/paths.py) resolves `data/` and `assets/` next to the
  executable for Nuitka/PyInstaller and to the repository root in a checkout.
  Do not reintroduce `__file__.parents[...]` arithmetic in the GUI or the
  downloader; `env_loader.is_frozen()` uses the same detection.
- [`pysidedeploy.spec`](pysidedeploy.spec) uses paths relative to the spec and
  lists Core/Gui/Widgets/Multimedia explicitly to avoid a project scan; Nuitka
  is pinned to 4.1.1 in the spec and in the `build` extra. A real build
  rewrites the spec in place (detected plugins, dropped blank lines), so do not
  commit that generated state. It also reparses the file with
  `ConfigParser(comment_prefixes="/", allow_no_value=True)`, so a `#` comment is
  a valueless *key*: keep the comments lowercase and free of `:` and `=`, or the
  rewrite splits them into a key/value pair.
- The bundle is pruned deliberately, because Nuitka's PySide6 plugin always adds
  its whole "sensible" Qt plugin set. `plugins = multimedia` stops
  pyside6-deploy's own detection from adding `platforminputcontexts`, which
  drags in QtVirtualKeyboard and the Qt Quick/Qml stack (~25 MB) that nothing
  loads; [`main.py`](main.py) additionally excludes the second OpenSSL keg
  (`libcrypto.4`/`libssl.4` plus the unversioned aliases, ~17.5 MB) that the
  macOS dependency walker copies although only `.3` is linked by `_ssl` and
  `_hashlib`. QtPdf (~9 MB) cannot be dropped this way: it arrives with the
  "sensible" `imageformats` set, not from detection.
- [`.github/workflows/release.yml`](.github/workflows/release.yml) builds four
  targets on a `v*` tag. `SCREENSCRAPER_ENV` (the exact three-line `.env`) is a
  repository secret compiled into `rc_data.py`; the tag version is stamped into
  `pyproject.toml` and the Windows metadata. The `.env` never reaches the
  release artifacts.
- macOS is two artifacts, not universal2: Nuitka's universal2 mode needs a
  universal interpreter and universal builds of every compiled dependency, and
  `rapidfuzz` ships per-architecture wheels. Intel pins PySide6 6.11 (macOS
  13), arm64 6.12 (macOS 14).
- ffmpeg comes from [`scripts/fetch_ffmpeg.py`](scripts/fetch_ffmpeg.py)
  (BtbN LGPL on Linux/Windows, evermeet.cx/OSXExperts GPL on macOS), is
  SHA-256 verified and git-ignored. Linux ships `.tar.gz` so the executable bit
  and library symlinks survive; Windows/macOS ship `.zip`. Only `ffmpeg` is
  fetched: the duration that sizes the preview cut is read from the cached MP3's
  frames with mutagen ([`downloader.probe_duration`](src/es_ost/downloader.py)),
  because a bundled static `ffprobe` is ~78 MB for one float and QtMultimedia's
  `QMediaPlayer.duration` is only available asynchronously on the GUI thread.
  The bundle therefore holds three FFmpeg-ish pieces by design — the static
  encoder, Qt's decoder, and nothing else — and PyAV would add a *fourth*
  (`av/.dylibs`, ~66 MB, no libvorbis), not replace any of them.

## Tracked data

- `data/` is a **committed** frozen dataset. `data/.cache/` is the scrape cache
  (hundreds of MB) and is never committed.
- `rank/rankings.json` (authoritative LLM scores) and `rank/mismatch_ok.tsv`
  (soundtrack/game pairs confirmed correct by hand) are committed. Every other
  `rank/` file is a working file and is ignored.
- `data/systems.json` is the authoritative rom-folder → ScreenScraper id
  mapping. It lives next to the frozen dataset so the single `data` include
  bundles it, but it is **source input**, not generated: `save_dataset` writes
  only the five JSON files, so a rebuild never touches it.
- Rebuilding the dataset is a data change: run `es-ost-prep build`, then
  `merge-rankings`, and commit the resulting `data/*.json` together with the
  `rank/rankings.json` that produced them. Never commit `data/.cache`.
- `docs/known-game-mismatches.md` records the soundtracks whose automatic match
  still points at a different game; a rebuild recomputes matches, so fix the
  matching rule rather than hand-editing `soundtracks.json`.

## ScreenScraper API notes

Base URL `https://api.screenscraper.fr/api2/`. Endpoints the code uses:

- `jeuRecherche.php` — search by name, up to 30 candidates. Returns `[{}]` for
  no results (normalize to `[]`); API result order is unreliable.
- `jeuInfos.php` — metadata by `gameid`: `note`, `roms[].nbscrap`.
- `systemesListe.php` — platform ids and names.
- `ssuserInfos.php` — login verification and the per-account quota
  (`maxrequestspermin`, `maxrequestsperday`, `requeststoday`, `maxthreads`,
  `maxdownloadspeed`).

HTTP errors: 400 bad params, 401 API closed to non-members, 403 bad developer
credentials, 404 not found, 423 API closed, 426 softname blacklisted, 429 rate
limit, 430 daily quota, 431 unrecognized-rom limit.
[`ScreenScraperError.fatal`](src/es_ost/prep/screenscraper.py) separates
session-level failures (abort the run) from per-item ones (log, degrade,
continue).

The API may only be embedded in free, distributed applications; a `softname`
identifies the client and can be blacklisted (426) if it is non-compliant. The
app ships `devid`/`devpassword`/`softname` obfuscated in `rc_data`; users supply
their own `ssid`/`sspassword`.

Retrotracks (`https://retro.sx/`) serves static table pages. The scraper stores
`rttid` + metadata and resolves the `.mp3` hash at download time. The site's
platform tags are ignored — platform eligibility comes from ScreenScraper.

## Dataset pipeline

1. Scrape the Retrotracks index and each soundtrack page.
2. `jeuRecherche` per soundtrack title → sort/filter candidates →
   [`match_game`](src/es_ost/core/ranking.py) picks the winner. The eligible
   system ids are the union of every surviving candidate's platforms.
3. `jeuInfos` for every filtered candidate on a default system → `note` plus
   scrape count (sum of `roms[].nbscrap`).
4. Rank each platform's games by `scrape_count` desc, then `note` desc.
5. Compute the provisional keyword `theme_score`; `carry_forward` keeps prior
   authoritative `llm_score`s and marks only new/changed tracks `pending`.
6. Emit `data/` plus `rank/ranking_queue.json`: one entry per soundtrack with
   its pending tracks nested. Soundtracks with no eligible platform are not
   queued (auto assignment can never pick them).
7. The LLM ranking pass writes `rank/rankings.json`; `es-ost-prep
   merge-rankings` normalizes the accepted shapes, validates 0–100 and folds the
   scores into the dataset.

## System identity mapping

Every ES theme name gets its own output file. Two folders that share a
ScreenScraper id (`megadrive`/`genesis` → 1, `gbc`/`gbch` → 10,
`sfc`/`snes` → 4, `famicom`/`nes` → 3) are still separate
[`ThemeSystem`](src/es_ost/core/models.py) entries with separate assignments;
the dedup policy in automatic mode gives them different soundtracks. Hack and
variant systems are not aliases — they are independently assignable, even when
they include the canonical system's XML.

Keys whose id `systems.json` cannot resolve, and non-standard kinds:

| Theme / rom-folder key | Canonical key | Screenscraper id | Kind |
|---|---|---|---|
| snesh | snes | 202 (distinct) | hack |
| nesh | nes | 3 | hack |
| genh | genesis | 1 | hack |
| gbh | gb | 9 | hack |
| gbch | gbc | 10 | hack |
| ggh | gamegear | 21 (fallback; `null` in the file) | hack |
| snes-msu1 | snes | 210 | variant |
| sfc | snes | 4 | alias/regional |
| famicom | nes | 3 | alias/regional |

`screenscraper` values in `systems.json` are heterogeneous (`int`, quoted
string, `null`, `""`); [`normalize_screenscraper`](src/es_ost/core/systemmap.py)
coerces them to `int | None`.

## Non-obvious gotchas

- ScreenScraper `note` = community score (0–20); `rating` = PEGI/ESRB age
  rating — never conflate. `jeuInfos` sends `note` as `{"text": "17"}` or
  `null` — never feed the dict to `int()` ([`_scalar`](src/es_ost/prep/build_dataset.py)
  unwraps it), and `systemesListe` nests platform names under `noms`
  (`nom_eu`/`nom_us`/`nom_recalbox`).
- Game titles live in `noms` (JSON: array of `{region, text}`; the `ss` region is
  the standardized name); `nom` is the internal-name fallback.
- Scrape count = sum of `roms[].nbscrap` from `jeuInfos` (not `jeuRecherche`,
  which omits roms). `build` fetches `jeuInfos` only for the *filtered search
  candidates on `in_defaults` systems* (~1.2k ids today, ~4.3k if every platform
  were covered; not just the match winner: each candidate carries its own
  `systeme.id`, so the Genesis port is what represents the soundtrack on the
  Genesis theme). `games_by_platform.json` therefore holds a real per-platform
  scrape ranking of the default systems' relevant games.
- API result order is unreliable — sort by
  [`ranking.candidate_score`](src/es_ost/core/ranking.py), the best
  `fuzzy_score` over *every* `noms` title (ss + regional), which is also the
  score [`match_game`](src/es_ost/core/ranking.py) picks the winner with;
  ordering and matching must never use different notions of score.
- `ss`-name normalization ([`ranking.standard_form`](src/es_ost/core/ranking.py)):
  `": "/" : "` → `" - "`, `&` → `And`, `+` → `Plus`, roman numerals → digits,
  and a leading `The` moved to the end ("The Killing Game Show" → "Killing Game
  Show, The") — but `X` stays a letter ("Mega Man X", "F-Zero X") and a leading
  numeral is left alone ("V-Rally").
  [`query_variants`](src/es_ost/prep/screenscraper.py) searches the standardized
  form, then that form with the article before the dash ("The Sims 3: Pets" →
  "Sims 3, The - Pets" — ScreenScraper files the same game both ways depending
  on the platform), then the name as given, then the pre-colon prefix. The
  original name must come before the subtitle-free prefix: otherwise a title
  that only exists under its full name matches the wrong, prefix-named game.
  [`candidate_score`](src/es_ost/core/ranking.py) normalizes *both* sides so
  `&`/`And` spellings and any article position still score 100.
- Search candidates are narrowed before matching
  ([`ranking.filter_candidates`](src/es_ost/core/ranking.py),
  `--min-candidate-score`): an absolute floor (85) plus a 5-point band below
  the best candidate. `WRatio` scores a mere prefix extension at exactly 90.0,
  so the band is what drops the rest of a series when the base game is present,
  while the floor alone would keep it. A sequel number that differs (X5 vs X)
  still scores ~95 — that is why `docs/known-game-mismatches.md` exists.
- Login verification also sets the throttle:
  [`verify_login`](src/es_ost/prep/screenscraper.py) calls `apply_quota()`,
  which spaces requests by `60 / maxrequestspermin` — a 1/min account waits
  60s, a 600/min account 0.1s — and leaves the constructor's `min_delay` alone
  when the quota reports `0`. Only runs that verify (`--verify-login`) get the
  adapted throttle; the value is exported read-only as
  `ScreenScraperClient.min_delay`.
- Output is one file per ES theme name in the project's `audio_format` (`mp3`
  default, `ogg`, `wav`), not per ScreenScraper id. The extension is part of the
  theme contract (`bgsound` is a literal path), and tags (title/artist/album)
  are written explicitly with `-metadata` rather than relying on the source:
  Batocera's `id3v2lib` reads ID3 only, and Retrotracks MP3s are often untagged.
- `rank/ranking_queue.json` is **soundtrack-rooted** (one entry per soundtrack,
  pending tracks nested) and omits soundtracks with an empty
  `eligible_screenscraper_ids`: auto assignment reads only that set, so those
  tracks stay `pending` but unqueueable. Skip on the eligible set alone —
  `matched_game_id = null` does **not** make a soundtrack unusable.
  `rankings.json` may be flat, a map, or soundtrack-keyed;
  [`flatten_rankings`](src/es_ost/prep/build_dataset.py) normalizes and
  validates (0–100) before anything is written, and a malformed file exits
  non-zero.
- `matched_game_id` is an **`int`** in the model:
  [`match_soundtrack`](src/es_ost/prep/build_dataset.py) and
  [`soundtrack_from_dict`](src/es_ost/core/serialize.py) coerce it, because
  `jeuRecherche` returns ids as strings. Stored verbatim, a string id silently
  disabled the `scrape_count` tiebreak in
  [`auto_assign`](src/es_ost/core/assign.py) and the game-name lookup of the
  ranking queue (both key on `Game.id`).
- HTTP responses are cached permanently under `<data-dir>/.cache` via
  [`PageCache`](src/es_ost/prep/cache.py) — ScreenScraper JSON and Retrotracks
  soundtrack pages. Two requests are never cached: the Retrotracks `/music`
  index (a cached index would hide new soundtracks) and `/rest` trackinfo
  (hashes rotate). `es-ost-prep clear-cache` wipes the directory.
- Only validated ScreenScraper bodies are cached:
  [`parse_api_response`](src/es_ost/prep/screenscraper.py) retries a plain-text
  error page or a header with `success != "true"` and never stores it, so a
  failed run cannot poison the cache.
- Builds degrade per item, print a summary and exit non-zero via
  [`RunStats`](src/es_ost/prep/build_dataset.py); `MAX_CONSECUTIVE_FAILURES`
  aborts against a dead API.
- Dev credentials live in the root `.env` (base64), loaded via
  `python-dotenv`; users supply their own `ssid`/`sspassword`.

## Tests

- Run the suite with `.venv/bin/python -m pytest -q`.
- Captured upstream pages live in `tests/fixtures/`; parser tests must keep
  using those real captures rather than synthetic HTML.
- `scripts/rank_batch.py` is loaded by path in `tests/test_rank_batch.py`; keep
  its module-level code import-safe and side-effect free.
- There are no automated GUI tests. Smoke-test the window with
  `QT_QPA_PLATFORM=offscreen` and the committed `data/` directory.

## Commits

- A successfully completed task or subtask must be committed to git before it
  is reported as done — do not leave finished work sitting in the working tree.
- Stage only what the task touched; do not sweep unrelated pending changes into
  the commit.
- Write a commit message that explains the change for a future reader who was
  not in the session: a `type(scope): summary` subject line (the type prefix
  matches the existing history: `feat`/`fix`/`docs`/`refactor`/`test`/`data`),
  a blank line, then a body of short paragraphs saying **what** changed, **why**
  (the failure mode or requirement being addressed) and any **non-obvious
  consequence** (behavior changes, new CLI exit codes, new files). Keep the
  subject under ~72 characters and the body wrapped at ~80.
- Run the test suite before committing; never commit a red tree.
- One commit per logical change: if a fix depends on prior uncommitted work in
  the same files, either commit that work first or say so in the body — never
  leave a commit that imports a module it does not add.

## Skills

- Mode-filtered Python + Qt skills live in [`.roo/skills/`](.roo/skills/).
