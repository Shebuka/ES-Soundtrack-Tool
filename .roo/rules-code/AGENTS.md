# AGENTS.md

Code-mode guidance for this repository. Only non-obvious, project-specific rules.

- The app is implemented in [`src/es_ost/`](../../src/es_ost); follow the existing layering (`core` = no Qt/network, `prep` = CLI + network, `gui` = Qt). The binding reference is [`AGENTS.md`](../../AGENTS.md).
- The rom-folder mapping is shipped as package data at [`src/es_ost/resources/systems.json`](../../src/es_ost/resources/systems.json); normalize its heterogeneous `screenscraper` field to `int | None`.
- ScreenScraper `note` = community score (0–20); `rating` = PEGI/ESRB age rating. `note` arrives as `{"text": "17"}` or `null`; platform names live under `systemesListe[].noms`.
- Game titles come from the `noms` array (the `ss` region is standardized); scrape count = sum of `roms[].nbscrap` via `jeuInfos`, fetched only for soundtracks' filtered candidates (each carries its own platform; no CSV scan).
- `jeuRecherche` returns `[{}]` for no results; API order is unreliable — sort by `candidate_score` (best `fuzzy_score` over every `noms` title), the same score `match_game` uses to pick a winner.
- `ss`-name normalization (`ranking.standard_form`): `":"` → `" - "`, `&` → `And`, `+` → `Plus`, roman numerals → digits, and a leading `The` moved to the end ("Killing Game Show, The"), except `X` ("Mega Man X") and a leading numeral ("V-Rally"). `query_variants` tries the standardized form, then that form with the article before the dash ("Sims 3, The - Pets"), then the original name, then the pre-colon prefix; `candidate_score` normalizes both sides.
- Candidates are filtered before matching (`ranking.filter_candidates`, `--min-candidate-score`): floor 85 plus a 5-point band under the best candidate, because `WRatio` scores a prefix extension at exactly 90.0.
- `verify_login()` throttles the client to the account's `maxrequestspermin` (`apply_quota`, `min_delay = 60 / limit`); a quota of `0` keeps the constructor value.
- Emit one file per ES `theme_name` in the project's `audio_format` (`mp3` default, `ogg`, `wav`); shared ids still produce separate files. Write title/artist/album tags explicitly (Batocera reads ID3 only).
- Consult the coding-convention skills in [`.roo/skills/`](../../.roo/skills/) (`python-design-patterns`, `python-type-safety`, `python-error-handling`, `python-resilience`, `qt-ui-design`).
- Commit every successfully completed task or subtask before reporting it done: `.venv/bin/python -m pytest -q` must pass, stage only the files the task touched, and use a `type(scope): summary` subject plus a body explaining what changed, why, and any non-obvious consequence (see the `## Commits` section of [`AGENTS.md`](../../AGENTS.md)).
