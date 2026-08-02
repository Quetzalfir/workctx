# Phase 1 consolidation — 2026-08-02

Lead-orchestrator consolidation batch. Contents:

| File | What |
| --- | --- |
| `state-of-repo.md` | What exists / what is facade / what is missing, file:line |
| `decisions-closed.md` | Operator-facing extract of every closed decision (never re-ask) |
| `contract-freeze.md` | The four frozen contract surfaces for the rest of Phase 1 |
| `docs-claims.md` | Facade/stale documentation claims inventory |
| `brief-cli-wiring.md` | Package brief LEAD-W1 (9 ready-engine CLI commands) |
| `brief-mcp-config-seam.md` | Package brief LEAD-W2 (close the agents MCP-config seam) |
| `report-cli-wiring.md` | (worker output, lands on delivery) |
| `report-mcp-seam.md` | (worker output, lands on delivery) |

## Batch status

| Package | Worker | Worktree/branch | State |
| --- | --- | --- | --- |
| WP-310 inbox lifecycle (r3) | Codex gpt-5.6-sol, max | `.worktrees/WP-310-r3` / `agent/WP-310-inbox-lifecycle-r3` | running |
| LEAD-W1 CLI wiring | Codex gpt-5.6-sol, max | `.worktrees/LEAD-W1-cli-wiring` / `lead/cli-wiring` | running |
| LEAD-W2 MCP-config seam | Codex gpt-5.6-sol, max | `.worktrees/LEAD-W2-mcp-seam` / `lead/mcp-config-seam` | running |
| Docs refresh | Claude agent | (after the three above integrate) | queued |

Integration order on delivery: WP-310 → W1 → W2 → docs. Lead reviews every diff,
captures commits (Codex sandbox cannot write worktree .git), runs the combined gate.

## Open decisions for the operator

See the chat message of 2026-08-02; summarized: GitHub remote/push (closes WP-001),
Wave 4 go, coverage threshold (D-017 due), MCP placeholder mutation-flag refinement.
