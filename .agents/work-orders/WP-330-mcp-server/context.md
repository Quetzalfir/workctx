# Work-order context: WP-330-mcp-server

## Why this exists

MCP is the model-neutral tool boundary (ADR 0003): any supported agent must operate a
context through the same tools. ADR 0012 froze the alpha surface after operator
ratification — your job is faithful implementation, not surface design.

## Required architecture and decisions

- ADR 0012 is normative: tool list, approval parameter, NOT-IMPLEMENTED placeholders,
  no prompts, stdio only, versioned schemas (R-017).
- doc-07 "MCP integration tests" section: discovery, input validation, resources,
  context scoping, safe error mapping, approval parameters, no context escape, stdio
  lifecycle.
- D-016 lifts with you: the mcp extra becomes dev/CI-installed because your code
  imports it; the runtime dependency stays an optional extra.

## Existing implementation

- Every backing engine is integrated and documented: validation
  (validate_workspace + FreshnessProbe), projection (SQLiteProjection: search,
  task queries, rebuild), retrieval (resolve/related/trace/build_pack +
  serialize_context_pack), transactions (validate_proposal/dry_run/apply/
  audit_summary/verify_ledger — read docs/reference/transactions.md), ingestion
  may land in parallel (WP-310) — its tools go through the same NOT-IMPLEMENTED
  placeholder if it has not integrated when you finish, coordinated via the lead.
- The CLI presentation layer shows the sanitization and diagnostic-code patterns to
  mirror (read src/workctx/presentation/, do not modify).
- `workctx mcp serve` slot: cli.py grants you a narrow edit — one sub-command that
  imports your package lazily (pattern: the existing index/ref commands).

## Dependencies

- Starts after WP-300 integrates. WP-310 may run in parallel — disjoint paths; if its
  APIs are integrated before your acceptance, wire inbox_list/artifact_register live;
  otherwise ship the placeholder and record it (the lead finalizes at integration).

## Known risks and edge cases

- The mcp pin (mcp[cli]>=2,<3) has never been installed in this repo (D-016): resolve
  early; a resolution failure is a stop condition, not something to work around with a
  different dependency.
- Windows stdio: ensure clean shutdown without orphan processes; the SDK client test
  must pass on Windows.
- Lazy-import the SDK so `workctx` without the extra works everywhere except
  `mcp serve`, which errors clearly (unavailable dependency).
- Keep tool schemas hand-written and minimal (ADR 0008 spirit); do not autogenerate
  from Pydantic internals.
- approval gating is structural (schema-level required field) AND runtime-checked.
- New test directory `tests/mcp/` needs an `__init__.py`; SDK-dependent tests must
  skip cleanly (with a recorded reason) when the extra is absent so the base gate
  stays runnable.
