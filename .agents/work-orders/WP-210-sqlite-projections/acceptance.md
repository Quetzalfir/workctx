# Acceptance criteria: WP-210-sqlite-projections

## Functional

- [ ] Schema DDL + projection metadata table with projection schema version.
- [ ] Full rebuild from canonical files via domain-model parsing; counts and skipped
      documents reported.
- [ ] Entities, edges, backlinks, observations, claims, tasks, aliases indexed; FTS5
      full-text search with documented tokenizer.
- [ ] Typed query APIs: entity by id/uri, outbound/inbound edges, FTS ranked search,
      task state queries.
- [ ] Temp-build-and-swap rebuild (os.replace) with clean reader reopen.

## Negative and edge cases

- [ ] Version mismatch → full rebuild (tested).
- [ ] Delete database → rebuild restores identical query results (rebuild equivalence,
      run twice).
- [ ] Two-context denial test: zero cross-context rows or results.
- [ ] Malformed canonical document skipped and reported; rebuild completes.
- [ ] Backlinks exactly mirror outbound references on fixtures.
- [ ] Observation ids with '#' round-trip in exact-match queries.

## Quality

- [ ] stdlib sqlite3 only; SQL confined to the adapter; frozen paths untouched.
- [ ] docs/reference/projections.md documents zones indexed, tokenizer, swap semantics.
- [ ] Fictional fixtures only.

## Commands

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```
