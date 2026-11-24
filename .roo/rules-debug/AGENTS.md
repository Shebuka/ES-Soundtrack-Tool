# AGENTS.md

Debug-mode guidance for this repository. Only non-obvious, project-specific rules.

- Run tests with `.venv/bin/python -m pytest`; launch the GUI headless with `QT_QPA_PLATFORM=offscreen`.
- ScreenScraper error semantics are summarised in [`AGENTS.md`](../../AGENTS.md): 403 = bad devid, 426 = softname blacklisted, 429/430/431 = rate/quota exceeded, 401/423 = API closed.
- Quota fields: `maxrequestspermin`, `maxrequestsperday`, `requeststoday`, `maxthreads`, `maxdownloadspeed` (from `ssuserInfos.php`).
- A no-match name search returns `[{}]` — not `[]` and not a 404 — see [`screenscraper.search`](../../src/es_ost/prep/screenscraper.py) normalization.
