# AGENTS.md

Ask-mode guidance for this repository. Only non-obvious, project-specific rules.

- [`AGENTS.md`](../../AGENTS.md) is the canonical developer reference: invariants, dataset pipeline, ScreenScraper API notes and error semantics.
- [`src/es_ost/resources/systems.json`](../../src/es_ost/resources/systems.json) is the authoritative rom-folder → ScreenScraper id mapping.
- [`tests/fixtures/`](../../tests/fixtures) holds read-only captures of real upstream pages; [`data/`](../../data) is the committed frozen dataset.
- The implementation lives in [`src/es_ost/`](../../src/es_ost): `core/` is pure logic, `prep/` is the CLI, `gui/` is Qt.
