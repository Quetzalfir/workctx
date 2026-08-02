# Frozen contract surfaces — Phase 1

The product has no UI; its "endpoints and DTOs" are the CLI envelope, the MCP tool
surface, the public JSON Schemas, and the cross-package Python APIs. All four are
FROZEN as of 2026-08-02 for the remainder of Phase 1: extensions are additive; any
break is an ADR revision, never a worker decision.

## 1. CLI envelope (public automation contract)

- Schema: `schemas/cli-envelope.schema.json`; docs: `docs/reference/cli-envelope.md`.
- Shape: `{ok, command, context_id, result(object), warnings[], errors[],
  meta{schema_version:1, duration_ms}}`; one JSON document on stdout; diagnostics on
  stderr; exit codes per D-015.
- Registered commands (frozen names): `version`, `doctor`, `context
  init|inspect|validate`, `validate` (alias, identity `context.validate`), `index
  rebuild`, `ref show|related|trace`, `context-pack`, `mcp serve`.
- Wave 3/4 additions (names reserved, lead wires): `inbox add|list`, `artifact
  show|verify`, `proposal validate|show`, `transaction apply|history|show`, `task
  list|show`, `brief`, `agent detect|install|status|open`, `search`, `view rebuild`.

## 2. MCP surface (ADR 0012, schema_version 1)

- 11 read tools + 6 approval-gated mutation tools, stdio, per
  `docs/reference/mcp.md`; contracts in `src/workctx/mcp/contracts.py`.
- NOT-IMPLEMENTED placeholders (stable names): `inbox_list`, `artifact_register`
  (→ WP-310), `draft_save` (→ WP-420), task enrichment (→ WP-400).

## 3. Public JSON Schemas (`schemas/`, ADR 0008/0011)

reference, source-locator, observation, claim (WP-100) · context, entity, task,
artifact-manifest (WP-110) · transaction-proposal, audit-event (WP-300) ·
context-pack (WP-230) · cli-envelope (WP-120) · skill-frontmatter, skill-registry,
skill-adapter-manifest (WP-130/320). Every change ships schema+model+fixtures together.

## 4. Cross-package Python APIs (frozen signatures)

- `workctx.domain` re-exports (44 symbols), `WorkctxUri` API.
- `workctx.services.contexts`: the four public functions.
- `workctx.validation`: `validate_workspace(root, *, strict, freshness_probe)`,
  report/issue fields, `contains_possible_secret`.
- `workctx.adapters.sqlite.SQLiteProjection`: queries, `rebuild`, `readiness_trigger`,
  `invalidate`; `projection_database_path`.
- `workctx.adapters.filesystem`: store/lock/staging (incl. move/delete kinds,
  `atomic_append_line_bytes`), registry.
- `workctx.transactions`: `validate_proposal`, `dry_run`, `apply`, `audit_summary`,
  `verify_ledger`, `authenticate_apply_result`, `ApplyResult` fields.
- `workctx.retrieval`: `resolve`, `related`, `trace`, `build_pack`,
  `serialize_context_pack`.
- `workctx.adapters.agents`: detect/install/status/repair/uninstall/open (per
  `docs/reference/agent-adapters.md`).
