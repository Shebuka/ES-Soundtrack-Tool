# AGENTS.md

Architect-mode guidance for this repository. Only non-obvious, project-specific rules.

- Behaviour and workflow are documented in [`README.md`](../../README.md); invariants and the data pipeline live in [`AGENTS.md`](../../AGENTS.md), which is the binding reference.
- Layering: `core/` (no Qt/network) → `prep/` (CLI + network) → `gui/` (Qt). Keep dependencies pointing inward.
- Two dimensions must not be conflated: ranking eligibility is per ScreenScraper id, but output is per ES theme name.
- Dev creds live in the root `.env` (base64, loaded via python-dotenv); user creds (`ssid`/`sspassword`) are required for Refresh and verified via `ssuserInfos.php`.
- LLM ranking is incremental: only new/changed tracks are marked `pending`; prior scores are never invalidated.
