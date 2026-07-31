# Acceptance criteria: WP-310-inbox-lifecycle

## Functional

- [ ] Registration writes a schema-valid manifest (SHA-256, media type, origin, dates,
      status) via the canonical store.
- [ ] Duplicate policy by content hash, both refuse and link cases mapped to the
      manifest vocabulary.
- [ ] Quarantine for injection markers, executables, oversized/unsupported types;
      location-only diagnostics.
- [ ] archive_after moves raw -> 01_processed only with a committed WP-300 receipt.
- [ ] register/list_inbox/quarantine_info/archive_after typed APIs.

## Negative and edge cases

- [ ] No receipt (or a receipt not referencing the artifact) -> no move.
- [ ] Interrupted move recoverable; no half-moved originals.
- [ ] Suspicious content never parsed/echoed; quarantine fails closed.
- [ ] Re-running register and archive_after is idempotent.

## Quality

- [ ] E2E-002 shape covered end-to-end in tests.
- [ ] docs/reference/inbox.md documents lifecycle, policies, and size guard.
- [ ] Frozen paths untouched; fictional fixtures; no new runtime deps.

## Commands

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```
