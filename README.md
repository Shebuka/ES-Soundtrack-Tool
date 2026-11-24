# es-soundtrack-tool

Build a personalized per-system background-music soundtrack set for any
EmulationStation theme.

![ES Soundtrack Tool main window](screenshots/ES%20Soundtrack%20Tool.png)

*The single systems view: favourites first, then the other systems, each row
with its soundtrack and track, plus the automatic assignment and export
controls.*

The end product is one audio file per EmulationStation **system theme name**
(e.g. `snes.mp3`, `snesh.mp3`, `sfc.mp3`) dropped into the theme's
`_art/sounds/` folder, so the theme's `bgsound` path
(`./_art/sounds/${system.theme}.mp3`) picks it up.

Each file is loudness-normalized (EBU R128, -16 LUFS), cut to a short preview
with a fade-out, and tagged with title/artist/album from the dataset. The
export format is a project setting: **MP3** (default), **OGG** or **WAV**. MP3
is the default because Batocera and several other ES builds read music metadata
from ID3 tags only and ignore Vorbis comments, so an `.ogg` background track
shows no title or artist. Whichever format is picked, the theme's `bgsound`
path must use the same extension.

A normal checkout already contains the frozen dataset and the LLM rankings, so
you can browse soundtracks, preview tracks and export a theme without any
scraping or ScreenScraper account.

## Quick start (from source)

Requirements: Python 3.11+ (3.12 recommended for the PySide6 wheels) and
`ffmpeg`/`ffprobe` on your `PATH` (or a static pair in `assets/ffmpeg/`).

```sh
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
es-soundtrack-tool
```

Without a display (CI, containers, remote shell), run it offscreen:

```sh
QT_QPA_PLATFORM=offscreen python -c \
  "import sys; from PySide6.QtWidgets import QApplication; \
   from es_ost.gui.main_window import MainWindow; \
   app = QApplication(sys.argv); MainWindow().show(); app.exec()"
```

## Using the app

1. **New project…** starts from the default ArkOS/RetroArch easyroms system set
   or from an empty project. Systems can be added with **Add system…** and
   pinned to the top with the favourite star.
2. The table lists every system with its soundtrack and track. Use the per-row
   picker to choose a soundtrack and a track; the row also has an inline
   preview player, so you can audition before committing.
3. **Automatically assign soundtracks** fills every unlocked system in one go:
   favourites first, least-used soundtrack first, and a track scored by the
   authoritative LLM ranking. A locked row keeps the choice you made by hand.
4. **Output settings…** chooses the audio format and the preview length
   (default 60 s; `0` keeps the full track).
5. **Export soundtrack…** writes `${system.theme}.<format>` for every
   assignment into the folder you pick. Copy the files into your theme's
   `_art/sounds/` directory. Export also keeps an on-disk MP3 cache, so
   re-exporting the same tracks is fast.
6. **Save project** / **Save as…** store the systems, favourites, assignments
   and output settings as versioned JSON; **Open project…** restores them.

Assigning `snes` also writes `sfc.<format>`, and `nes` also writes
`famicom.<format>` (regional aliases that share the same ScreenScraper id).
Hack and variant systems are **not** aliases: `snesh`, `nesh`, `genh`, `gbh`,
`gbch`, `ggh` and `snes-msu1` are independently assignable and get their own
file.

### Refresh data

**Data → Refresh data…** takes your own ScreenScraper `ssid`/`sspassword`,
verifies the login and reads the account's quota (requests per minute/day,
threads, download speed). The developer credentials needed for the API are
shipped obfuscated; you never need your own developer key.

Rebuilding the dataset itself is a maintainer task (below): the scraping cache
and the ranking pass are deliberately not part of the user-facing refresh.

## The dataset

`data/` is a committed frozen dataset (927 soundtracks, ~28k tracks, 293 theme
systems, built 2026-11-17):

```
data/
  version.json           dataset version + build date
  platforms.json         screenscraper_id -> display name
  theme_systems.json     theme_name -> rom_folder, screenscraper_id, kind, defaults
  soundtracks.json       soundtrack -> matched game, eligible ids, tracks and scores
  games_by_platform.json screenscraper_id -> candidate games ranked by scrapes/note
rank/
  rankings.json          authoritative LLM theme-song scores
  mismatch_ok.tsv        soundtrack/game pairs confirmed correct by hand
```

`data/.cache/` (the scraped ScreenScraper/Retrotracks responses) and the
ranking work files are git-ignored. A dataset rebuild is a data change: commit
the resulting `data/*.json` together with the `rankings.json` that produced
them.

## Maintainer guide

### Building the dataset

The prep CLI reads the root `.env` through
[`env_loader.py`](src/es_ost/core/env_loader.py), so the developer credentials
live in one place. `SCREENSCRAPER_*` environment variables or CLI flags
override the `.env`; the user-level `ssid`/`sspassword` come from
`SCREENSCRAPER_SSID`/`SCREENSCRAPER_SSPASSWORD` or flags.

```sh
es-ost-prep build --data-dir data --rank-dir rank --verify-login
```

The build scrapes the Retrotracks index, matches each soundtrack title against
ScreenScraper, fetches `jeuInfos` for every filtered candidate on a default
system, and emits `data/` plus `rank/ranking_queue.json`. The queue is **one
entry per soundtrack** with its pending tracks nested; soundtracks with no
eligible ScreenScraper platform are left out because automatic assignment can
never pick them.

ScreenScraper responses and Retrotracks soundtrack pages are cached under
`data/.cache` (override with `--cache-dir`), so re-running `build` only fetches
what is missing. Two requests are deliberately never cached: the Retrotracks
`/music` index (a cached index would hide newly added soundtracks) and the
`/rest` track-hash resolution (hashes rotate upstream, and it runs once per
exported track). Delete the cache to force a full re-fetch:

```sh
es-ost-prep clear-cache --data-dir data
```

A transient ScreenScraper problem degrades only the item it hit: the build logs
it, prints a summary and exits non-zero, so an incomplete dataset is not
mistaken for a clean run. Session-level failures (bad credentials, blacklisted
softname, closed API, exhausted quota) still abort immediately. Nothing from a
failed request is cached, so re-running the build retries those items.

### Ranking the tracks

Theme-song ranking is too subtle for a keyword list, so the authoritative score
comes from an LLM pass over the queue. Use the helper rather than ad-hoc
snippets:

```sh
# Print the next batch of queued soundtracks with the context a ranker needs.
.venv/bin/python scripts/rank_batch.py dump --count 50 > rank/batch02_prompt.txt

# Turn the compact judgement file into the rankings.json the merge expects.
.venv/bin/python scripts/rank_batch.py expand \
    --judgements rank/batch02_scores.json --rankings rank/rankings.json

# Fold the scores into the dataset.
es-ost-prep merge-rankings --data-dir data --rankings rank/rankings.json

# List ranked soundtracks matched to a different game (for a manual id pass).
.venv/bin/python scripts/rank_batch.py mismatches
```

`rankings.json` is accepted as a flat `[{"rttid": ..., "llm_score": ...}]` list,
a `{rttid: score}` map, or a soundtrack-keyed mapping/list mirroring the queue.
Scores must be 0–100; a bad entry makes the merge exit non-zero without touching
the dataset. Re-merging a ranked track overwrites its score, which is how a bad
ranking is corrected.

Ranking is incremental: only tracks with `rank_status = pending` are queued, so
re-running the prep CLI preserves existing scores. `docs/known-game-mismatches.md`
records the soundtracks whose automatic match still points at a different game;
fix the matching rule rather than hand-editing `soundtracks.json`.

### Matching

A soundtrack title is searched in the ScreenScraper-standardized spelling
(`: ` → ` - `, `&` → `And`, `+` → `Plus`, roman numerals → digits, a leading
`The` moved to the end, but `X` stays a letter). The variants tried, in order,
are the standardized form, the form with the article before the dash, the name
as given, and finally the pre-colon prefix. Results are narrowed before the
match is chosen: anything scoring below `--min-candidate-score` (default 85) is
dropped, and so is anything more than 5 points behind the best candidate. The
second cut keeps a search for the first game of a series from dragging the whole
series into the eligible-platform set, while still keeping subtitled-only names
when nothing better exists.

### System identity mapping

`screenscraper` ids come from
[`src/es_ost/resources/systems.json`](src/es_ost/resources/systems.json), a
rom-folder → ScreenScraper id table shipped as package data. Keys the file
cannot resolve fall back to a canonical system:

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

Every theme name gets its own output file even when two folders share an id
(`megadrive`/`genesis` → 1, `gbc`/`gbch` → 10, plus the aliases above).

### Tests

```sh
.venv/bin/python -m pytest -q
```

Captured upstream pages used by the parser tests live in `tests/fixtures/`.
There are no automated GUI tests; smoke-test the window offscreen against the
committed `data/` directory.

## Packaging

The app is packaged with `pyside-deploy` using
[`pysidedeploy.spec`](pysidedeploy.spec), which bundles `data/` and
`assets/` (put a static `ffmpeg`/`ffprobe` pair in `assets/ffmpeg/` for a
release build). The developer credentials are embedded separately through
`resource_data.qrc`:

```sh
pyside6-rcc resource_data.qrc -o src/es_ost/gui/rc_data.py
```

`.env` is the source for those credentials and is git-ignored; commit only
[`.env.example`](.env.example) (empty values) and inject the real values at
build time from a CI secret. The generated `src/es_ost/gui/rc_data.py` is also
git-ignored.

Running from source is the supported path today. The packaged build has two
open items to verify before a release: `pysidedeploy.spec` expects an
`assets/` directory that is not populated in a source checkout, and the
window resolves `data/` relative to the source tree.

## Project layout

```
src/es_ost/
  core/        domain model, system map, ranking, assignment, dataset/project IO
  prep/        Retrotracks + ScreenScraper scrapers and the dataset builder CLI
  gui/         PySide6 application (window, table, wizard, refresh, export)
  resources/   systems.json (rom-folder -> ScreenScraper id, package data)
  downloader.py  song download + ffmpeg transcode
data/          committed frozen dataset (cache under data/.cache is ignored)
rank/          committed rankings.json + mismatch_ok.tsv (work files ignored)
scripts/       rank_batch.py (ranking helper)
tests/         pytest suite, captured upstream pages in tests/fixtures/
docs/          known data issues
```

## Data sources and legal notes

- Soundtracks come from [Retrotracks](https://retro.sx/) and are downloaded
  on demand for personal use on your own device; the repository does not
  redistribute audio.
- Game metadata comes from the ScreenScraper Web API v2, which may only be
  embedded in free, distributed applications. The app ships a registered
  `softname`; users supply their own account credentials for refresh.
- The code is licensed under the GNU Affero General Public License v3.0 or
  later (AGPL-3.0-or-later), see [`LICENSE`](LICENSE). Commercial use is
  permitted, but anyone who distributes a modified version — or runs one as a
  network service — must offer the complete corresponding source under the
  same license. Copyright (C) 2026 Shebuka.
- `systems.json` comes from `emulationstation_gamemanager` and keeps its
  upstream notice; the Retrotracks and ScreenScraper data is governed by their
  terms as described above.
