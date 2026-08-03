# Brief: WP-400 — Tasks, claims, and generated operational views

Worktree `.worktrees/WP-400`, branch `agent/WP-400-tasks-views`. You cannot commit
(sandbox); leave changes uncommitted. Reports: your final message is the report; also
write `report-wp400.md` in THIS plan directory if writable, else skip (`.agents` is
usually read-only for you).

## Read first

AGENTS.md · `.agents/plan/initial/06-implementation-work-packages.md` §WP-400 ·
`docs/reference/transactions.md` · `docs/reference/projections.md` ·
`.agents/plan/2026-08-02_172559-phase1-consolidation/{decisions-closed,contract-freeze}.md`

## Scope

1. `src/workctx/tasks/`: a task-operations service where EVERY canonical mutation goes
   through `workctx.transactions` proposals (create task/subtask, status transition
   with a claim recording the change per doc-03 claims-and-time, owner/waiting_on
   updates, next_action). State history = claims (subject task URI, predicate status/
   owner/due, supersession on change). Parent/subtask integrity via the existing
   `validate_task_hierarchy`; stale-revision conflicts surface as-is.
2. `src/workctx/views/`: generated operational views written DIRECTLY (they are
   derived, rebuildable state — not proposals): `04_views/current-focus.md`,
   `04_views/next-actions.md`, `04_views/waiting-on.md`, `04_views/stale-knowledge.md`,
   and `04_views/brief.md` (the doc-04 `brief` payload). Each file starts with a
   generated-header (generator name, source revision = ledger head hash, timestamp
   passed in by caller — NO wall-clock defaults in domain logic; accept a clock like
   IngestionService does). Deterministic content given identical projection state.
   Rebuild-all API (`rebuild_views`) + per-view. Read data via SQLiteProjection typed
   queries + retrieval APIs only.
3. A `brief` payload API returning the structured daily brief (today-focus, blockers,
   waiting-on with people, stale claims, recent ledger activity) for the CLI/MCP
   wiring (lead wires the `workctx brief` command later — NO cli.py edits).
4. Validation interplay: views carry `generated_by` frontmatter; ensure the validator
   does not flag them (they live in 04_views; check engine treatment and, if it
   document-validates them, give views a compliant minimal frontmatter rather than
   touching the validator — `src/workctx/validation/**` is frozen for you).

## Do NOT touch

cli.py, presentation/, mcp/, validation/, adapters/ (consume APIs), domain/ (consume),
transactions/ engine, ingestion/, retrieval/ internals, schemas/** (task/claim schemas
are frozen; if a view needs a schema, document the shape in your reference doc
instead), `.agents/**`, pyproject.toml, other packages' tests.

## Tests required

`tests/tasks_views/` (+`__init__.py`; avoid basename collisions): task ops end-to-end
through real transactions on a fixture context (create→transition→reassign, claims
supersession chain verified via ledger+projection), hierarchy violations refused,
stale-revision conflict surfaces, views deterministic (two rebuilds byte-identical),
generated headers correct, views excluded from canonical validation issues, brief
payload structure. Full gate must pass: ruff check/format, mypy, pytest.

## Deliverables

Code + tests + `docs/reference/views.md` (view formats, header contract, brief
payload). Template additions for 04_views are NOT needed (files are generated).
