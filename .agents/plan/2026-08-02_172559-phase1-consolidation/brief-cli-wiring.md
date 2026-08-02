# Brief: LEAD-W1 — CLI wiring for ready engines

Worktree `.worktrees/LEAD-W1-cli-wiring`, branch `lead/cli-wiring`. You cannot commit
(sandbox); leave changes uncommitted, the lead captures.

## Scope (exactly these 9 commands, envelope-first)

Register in `src/workctx/cli.py`, following the EXISTING command templates
(`ref show` at cli.py:304 and `index rebuild` at cli.py:217 are the canonical
patterns: `begin_command` → `resolve_cli_context` → engine call → compact
`dict[str, JsonValue]` result → `emit_success`/`record_failure` + human line;
lazy-import engines inside the function; `--context` option everywhere):

1. `proposal validate <file> [--json]` → `workctx.transactions.validate_proposal`
   (engine.py:1704). Load the proposal file as JSON → `TransactionProposal.model_validate`.
   Command id `proposal.validate`.
2. `proposal show <file> [--json]` → parse + dry-run WITHOUT apply →
   `dry_run` (engine.py:1711); command id `proposal.show` (doc-04 pairing).
3. `transaction apply <file> [--dry-run] [--yes] [--json]` → `apply`
   (engine.py:1715) with `approved=bool(yes)`; WITHOUT `--yes` do a dry-run and print
   the intended changes + "re-run with --yes" (doc-04 mutation UX); `--dry-run` forces
   the non-apply path. Failure mapping: engine conflict errors already map to exit 4
   via the boundary. Command ids `transaction.apply` / same with dry_run flag in result.
4. `transaction history [--limit N] [--json]` → `audit_summary` +
   ledger events tail. Command id `transaction.history`.
5. `transaction show <transaction-or-event-id> [--json]` →
   `find_event_by_id` / `find_event_by_proposal_id` (ledger.py:96/106).
6. `search <query> [--type T]... [--limit N] [--json]` →
   `SQLiteProjection.search` (projection.py:562).
7. `task list [--status S]... [--waiting-on P] [--json]` →
   `SQLiteProjection.query_tasks` (projection.py:507) via `TaskQuery`.
8. `task show <task-id> [--json]` → `get_task` (projection.py:485).
9. `agent detect|status|install|open` →
   `detect_clients` (detection.py:189), `AgentAdapterService.status` (service.py:417),
   `plan_install`+`install` (service.py:1196/2124 — WITHOUT `--yes` show the plan and
   stop; `--yes` executes with approvals), `open_context` (session.py:31).

## Do NOT touch

Engines (`transactions/`, `adapters/`, `retrieval/`, `validation/`, `domain/`,
`services/`, `mcp/`), schemas EXCEPT you may NOT change `cli-envelope.schema.json`
(result stays object-shaped; new commands need no schema change), presentation/
internals (consume the helpers as the existing commands do), `.agents/**` except your
report, `pyproject.toml`, tests of other packages.

## Tests required

Extend `tests/cli/` (new file `test_wave3_wiring.py`, plus `tests/cli/__init__.py`
already exists): per command — envelope validity against the schema, exit codes
(happy, user-correctable, conflict for a stale-revision apply), stdout purity with
split streams, `transaction apply` without `--yes` never mutates (tree-hash compare),
`--yes` applies and the receipt authenticates via `authenticate_apply_result`.
Fixture workspaces via `initialize_context` + the support patterns in
`tests/transactions/support.py` (import with a relative `from .` only inside the same
package — for cross-package reuse, copy the tiny helpers you need instead of importing
across test packages).

## Deliverables

Updated cli.py + tests + `docs/reference/cli-envelope.md` command table update +
report at `.agents/plan/2026-08-02_172559-phase1-consolidation/report-cli-wiring.md`
(commands run + exact results). Full gate must pass:
`uv run ruff check .` / `uv run ruff format --check .` / `uv run mypy src` /
`uv run pytest`.
