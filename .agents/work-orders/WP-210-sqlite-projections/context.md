# Work-order context: WP-210-sqlite-projections

## Why this exists

The plan's decisive scenario is a fresh session recovering full operational context from
canonical files — which requires deleting and rebuilding all derived state (E2E-004,
NFR-004). SQLite/FTS is the only queryable index; WP-230 retrieval and WP-400 views are
blocked on your query APIs.

## Required architecture and decisions

- ADR 0001: SQLite is a rebuildable projection, never a source of truth.
- ADR 0007: projection schema mismatch → full rebuild, never in-place migration.
- 03-reference-and-retrieval-model.md: backlinks are derived from canonical outbound
  references; both directions must be queryable.
- D-014 note: incremental updates on mutation are WP-300's wiring; you ship full rebuild.

## Existing implementation

- Domain models (integrated): EntityFrontmatter, Task, Observation, Claim,
  TypedReference, RelationType, EntityType, WorkctxUri — parse and validate canonical
  documents through them; skip-and-report invalid ones.
- `workctx.domain.frontmatter` (lead-provided, frozen): the only frontmatter parser.
- Workspace layout: 02_knowledge/** and 03_work/** hold entity/task documents;
  99_meta/templates/ holds authoring templates (exclude them from indexing);
  tests/workspace/fixtures and the packaged context template are useful fixture sources.
- No SQLite code exists anywhere yet; `src/workctx/adapters/` was created empty by the
  lead (its `__init__.py` is frozen).

## Dependencies

- WP-100/WP-110 integrated on your base. WP-200 (parallel) owns 98_state lock/staging
  and filesystem write primitives — you never need them: your rebuild is read-only over
  canonical files and writes only your own database files, swapped via os.replace on a
  temp file you own (e.g. index.sqlite3.building).

## Known risks and edge cases

- Keep every SQL statement inside the adapter; callers get typed functions only.
- sqlite3 on Windows holds file handles — close connections before os.replace of the
  database; design the swap so concurrent readers reopen cleanly (URI mode / retry on
  reopen is acceptable and must be documented).
- Store URIs as canonical strings (str(WorkctxUri)); percent-encoding must round-trip.
- Observation ids contain '#'; be careful with FTS tokenization of ids — ids belong in
  exact-match columns, not the FTS table.
- Exclude 99_meta/templates and 00_inbox raw payloads from indexing; document what is
  indexed per zone.
- New test directory `tests/projections/` needs an `__init__.py`.
