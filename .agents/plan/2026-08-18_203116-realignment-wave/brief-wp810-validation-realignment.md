# Brief: WP-810 — Strict-validation poison fix, pydantic error surfacing, packaged proposal schemas (realignment wave)

Codex worker, worktree `.worktrees/WP-810`, branch `agent/WP-810-validation-realignment`.
No commits; final message = report. Origin: 2026-08-05 audit findings.

## Contract

1. Poison fix (transaction engine): strict content validation attached to
   transaction apply must be scoped to the files the transaction itself
   writes (validate-what-you-touch). A pre-existing violation in an
   UNRELATED workspace file (e.g. a legacy document containing a
   machine-absolute path) must not block future applies. Full-workspace
   strict checking remains available in `workctx context validate
   --strict` and is unchanged. Regression tests: (a) workspace with a
   legacy violating file -> unrelated apply succeeds; (b) a transaction
   that itself writes a violating file still fails with the existing
   diagnostic; (c) `context validate --strict` still reports the legacy
   file.
2. Pydantic error surfacing: when a proposal fails model validation (CLI
   `proposal validate`, `transaction apply`, and MCP mutation tools), the
   error output must include pydantic's field-level detail — field path
   and message per error — through the existing envelope/diagnostic
   shapes, passed through existing sanitization. No more opaque
   single-line rejections. Tests pin the JSON shape for at least CLI and
   one MCP tool.
3. Packaged proposal schema in contexts: the context template gains
   `99_meta/schemas/transaction-proposal.schema.json` (materialized from
   the canonical `schemas/` source at template-sync time — single source
   of truth; never hand-duplicated), and a new idempotent command
   `workctx context refresh-meta` materializes or updates these packaged
   reference schemas under `99_meta/schemas/` in an EXISTING context
   without touching any other file. `context validate` gains an info-band
   diagnostic with repair_action naming that command when the packaged
   schemas are missing or stale.

## Allowed paths

`src/workctx/transactions/**`, `src/workctx/validation/**`,
`src/workctx/mcp/**`, `src/workctx/cli.py` (proposal/transaction/context
commands + envelope helpers), `src/workctx/resources/context_template/
99_meta/**`, `scripts/sync_context_template.py` (only if schema
materialization requires it), `docs/reference/cli-envelope.md`,
`docs/reference/agent-adapters.md` is FORBIDDEN, bridges/skills are
FORBIDDEN (sibling package), `schemas/` is read-only source,
`tests/transactions/**`, `tests/validation/**`, `tests/mcp/**`,
`tests/cli/**` (new files only), `tests/agents_setup/test_context_template*`.

## Tests

As listed per piece; full gate where the sandbox allows, limits declared.
