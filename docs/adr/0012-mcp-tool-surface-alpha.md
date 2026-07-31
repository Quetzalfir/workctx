# ADR 0012: MCP tool surface for the first alpha

- Status: proposed
- Date: 2026-07-31

## Context

The architecture plan lists the exact first-alpha MCP tool surface as a decision to
confirm early (open decision D-020), and risk R-017 warns that MCP surface changes break
agents — the surface is a versioned public contract from its first release. WP-330
cannot be contracted without this list. The plan's MCP boundary principles: stable,
task-oriented tools rather than raw file-edit tools; read-only resources; skills remain
the richer workflow layer.

## Decision

### Principles

- Every tool is bound to exactly one context (the server is started per context root);
  no tool can address another context (ADR 0004).
- Tool inputs/outputs use explicit JSON Schemas with `schema_version: 1`; changes within
  the 0.x line are backward compatible or ship as new tool names (R-017).
- Structured errors reuse the CLI diagnostic codes (REF-NOT-FOUND, PACK-NOT-BUILT,
  CTX-*, PROJECTION-*) and the exit-band semantics mapped to error categories.
- Mutation tools take an explicit `approved: true` input parameter and fail without it;
  external writes do not exist in the alpha surface at all (drafts persist locally only).

### Read tools (v1)

| Tool | Backing engine |
| --- | --- |
| `context_info` | context config + doctor summary (WP-110/WP-120) |
| `workspace_validate` | validation engine, `strict` option (WP-220) |
| `search` | FTS query API (WP-210) |
| `ref_show` | retrieval resolve (WP-230) |
| `ref_related` | retrieval traversal (WP-230) |
| `ref_trace` | retrieval tracing (WP-230) |
| `context_pack` | pack builder with budget/query/history/architecture (WP-230) |
| `task_list`, `task_show` | task queries (WP-210 now; enriched by WP-400) |
| `inbox_list` | artifact manifests (WP-310) |
| `audit_summary` | ledger read (WP-300, ADR 0010) |

### Mutation tools (v1, approval-gated)

| Tool | Backing engine |
| --- | --- |
| `artifact_register` | inbox registration (WP-310) |
| `proposal_validate` | transaction proposal validation, no writes (WP-300) |
| `transaction_dry_run` | staged evaluation without apply (WP-300) |
| `transaction_apply` | atomic apply under lock (WP-300) |
| `index_rebuild` | projection rebuild — derived state only (WP-210) |
| `draft_save` | outbox persistence, local only (WP-420) |

### Resources (read-only)

- `workctx://<context>/entity/...` canonical entities (serialized frontmatter);
- context configuration and generated views as they land (WP-400).

### Prompts

- None in v1: portable skills own workflows (doc-13); MCP prompts may arrive post-alpha.

### Sequencing

- WP-330 implements the surface for engines that exist when it runs (Wave 3): everything
  except `task_*` enrichment and `draft_save`, which land with Wave 4 behind the same
  contract; tools whose engine is missing return a structured NOT-IMPLEMENTED error
  rather than being absent, so agent configs stay stable across the alpha line.

## Consequences

- WP-330's contract can enumerate exact tool schemas; WP-320's MCP config generation
  references a stable server identity (D-014 sequencing note stands).
- The surface is deliberately small; adding tools is cheap, removing them is a breaking
  change — additions post-alpha require only backward compatibility, not an ADR each.
- No external write exists to gate in v1, keeping the approval boundary simple
  (local transaction approval only).
