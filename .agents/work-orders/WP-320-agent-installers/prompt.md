# Worker assignment: `WP-320-agent-installers`

You are the worker assigned to `WP-320-agent-installers` in the Work Context OS
repository. You are working in the Git worktree `.worktrees/WP-320-agent-installers`
on branch `agent/WP-320-agent-installers`.

## Mandatory instructions

1. Read `AGENTS.md` at the repository root.
2. Read `.agents/work-orders/WP-320-agent-installers/contract.json`, `context.md`, and
   `acceptance.md` in this work-order directory.
3. Read every file listed under `required_reading` — doc-13 and the WP-130 manifest
   spec are your functional specification; a spec gap is a coordination request, never
   a schema edit.
4. Work only in the assigned worktree and branch; modify only `allowed_paths`, never
   `forbidden_paths`. Canonical skills, bridges (CLAUDE.md/GEMINI.md/AGENTS.md), and
   all schemas are frozen for you.
5. HARD SECURITY BOUNDARY: no code path may read, copy, or configure agent
   authentication credentials or user-global auth files. Include the negative test.
6. MCP configuration generation is deferred to after WP-330 (D-014): reserve the seam,
   report NOT-IMPLEMENTED, do not invent a server identity.
7. Generation must be idempotent by content hash; uninstall must remove only
   manifest-listed files; user-owned files get backups, never deletion.
8. Keep all repository artifacts in English. Communicate with the human operator in the
   language configured in `.agents/operator.local.yaml` when present.
9. Tests run in isolated fake home/project directories with fake executable discovery
   (doc-07); no real client may be required. A blocker is a valid result.
10. Before stopping, write `report.md` and `report.json` in this work-order directory,
    following `.agents/templates/work-order/` templates, with exact commands and
    results. A completion claim without executed command evidence will be rejected.

## Objective

Build `src/workctx/adapters/agents/`: detection for codex/claude/gemini, manifest-driven
adapter generation from canonical skills and bridges (per doc-13 and the WP-130 manifest
spec), drift-detecting status, targeted repair, safe uninstall with user-file backups,
and an open_context session bootstrap — all as typed APIs (CLI wiring is lead work),
with the MCP-config seam reserved for post-WP-330.

## Validation commands

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

If safe completion requires missing information, forbidden paths, or an architectural
change, stop and report a blocker instead of improvising.
