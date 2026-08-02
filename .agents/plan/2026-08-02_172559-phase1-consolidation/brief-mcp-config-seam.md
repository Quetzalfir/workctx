# Brief: LEAD-W2 — Finalize the agents MCP-config seam (D-014 close-out)

Worktree `.worktrees/LEAD-W2-mcp-seam`, branch `lead/mcp-config-seam`. You cannot
commit (sandbox); leave changes uncommitted, the lead captures.

## Context

WP-330 defined the server identity: `workctx mcp serve --context <root>` over stdio
(docs/reference/mcp.md has the client-config documentation). The agents adapter kept
its MCP component as a constant `not_implemented` seam. Close it.

Seam map (explorer-verified):
- `src/workctx/adapters/agents/manifest.py:223-229` — `McpConfigurationComponent`
  with `Literal["not_implemented"]` (schema admits nothing else).
- `src/workctx/adapters/agents/service.py:197-201` — `_feature_mcp()` constant
  producer; `service.py:1837` hard-coded manifest literal.
- `src/workctx/adapters/agents/models.py:34,176-181` — FeatureState enum +
  AdapterStatus default.

## Scope

1. Widen the component model/schema: states `not_implemented | generated | native |
   divergent` plus the generated config path and content hash, mirroring the existing
   bridge-component pattern in the same files.
2. Per-client project-scoped MCP config generation (D-032 authority + D-028
   generate-if-absent apply to these files exactly like bridges):
   - claude: `.mcp.json` mcpServers entry `workctx: {command: "workctx", args:
     ["mcp","serve","--context","."]}` (merge-preserving if the file exists and is
     user-owned: report divergent, never rewrite).
   - codex: `.codex/config.toml` `mcp_servers.workctx` equivalent (TOML).
   - gemini: `.gemini/settings.json` mcpServers equivalent.
   Follow each client's documented shape in docs/reference/mcp.md §client
   configuration; if that doc lacks a client's exact shape, use the shape the doc DOES
   give and flag any gap in your report rather than inventing.
3. `_feature_mcp()` becomes real detection (path + hash vs manifest, like
   `_feature_*` siblings); `service.py:1837` derives from the install plan.
4. Update `schemas/skill-adapter-manifest.schema.json` for the widened component
   (ADR 0008 fixtures, positive + negative) and
   `docs/reference/agent-adapters.md` + `docs/reference/skill-adapters.md` seam notes.
5. Kit prose validator: add an implemented-MCP-tool allowlist mirroring
   `_is_implemented_command` (`sources.py:397-422`; current unconditional flag at
   `sources.py:459-460`) sourced from the ADR 0012 tool names in
   `workctx.mcp.contracts`.

## Do NOT touch

`src/workctx/mcp/**` (identity is consumed, not changed), CLI, engines, canonical
skills, bridges of THIS repo, `.agents/**` except your report, `pyproject.toml`.

## Tests required

Extend `tests/agents_setup/`: generation per client with manifest recording +
D-032 authority honored (tampered trusted record → report-only for MCP files too),
existing user config preserved + divergent flagged, uninstall removes only
manifest-listed MCP files, allowlisted MCP tool names pass kit lint while unknown
ones still fail. Full gate must pass.

## Deliverables

Code + tests + docs + report at
`.agents/plan/2026-08-02_172559-phase1-consolidation/report-mcp-seam.md`.
