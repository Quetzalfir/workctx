# Worker assignment: `WP-330-mcp-server`

You are the worker assigned to `WP-330-mcp-server` in the Work Context OS repository.
You are working in the Git worktree `.worktrees/WP-330-mcp-server` on branch
`agent/WP-330-mcp-server`.

## Mandatory instructions

1. Read `AGENTS.md` at the repository root.
2. Read `.agents/work-orders/WP-330-mcp-server/contract.json`, `context.md`, and
   `acceptance.md` in this work-order directory.
3. Read every file listed under `required_reading` — ADR 0012 is normative: implement
   its surface exactly; changing it is an ADR revision, never a worker decision.
4. Work only in the assigned worktree and branch; modify only `allowed_paths`, never
   `forbidden_paths`. Your `cli.py` grant covers one `mcp serve` sub-command; your
   `pyproject.toml`/`ci.yml` grant covers only enabling the mcp extra for dev/CI.
5. Delegate every tool to the engines' public APIs; no engine edits — gaps are
   coordination requests to the lead.
6. Mutation tools require `approved: true` structurally and at runtime; there are no
   external writes in this surface.
7. Lazy-import the SDK; without the extra, everything but `mcp serve` keeps working
   and `mcp serve` errors clearly; SDK-dependent tests skip cleanly with a recorded
   reason.
8. Keep all repository artifacts in English. Communicate with the human operator in
   the language configured in `.agents/operator.local.yaml` when present.
9. Denial tests (cross-context, path escape), sanitization tests (no tracebacks or
   secret-looking values), and the stdio lifecycle test are contractual. A blocker is
   a valid result.
10. Before stopping, write `report.md` and `report.json` in this work-order directory,
    following `.agents/templates/work-order/` templates, with exact commands and
    results. A completion claim without executed command evidence will be rejected.

## Objective

Build `src/workctx/mcp/`: a stdio MCP server (official Python SDK) bound to one context,
exposing exactly the ADR 0012 surface — 11 read tools and 6 approval-gated mutation
tools delegating to the integrated engines, structured NOT-IMPLEMENTED placeholders for
Wave 4 dependents, read-only canonical resources, CLI-diagnostic-coded structured
errors, strict context scoping — plus the `workctx mcp serve` entry point, dev/CI mcp
extra enablement, and docs/reference/mcp.md.

## Validation commands

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

If safe completion requires missing information, forbidden paths, or an architectural
change, stop and report a blocker instead of improvising.
