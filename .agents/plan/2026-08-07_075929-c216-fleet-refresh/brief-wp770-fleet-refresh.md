# Brief: WP-770 — Fleet refresh across registered contexts (C-216)

Codex worker, worktree `.worktrees/WP-770`, branch `agent/WP-770-fleet-refresh`.
You cannot commit; leave changes uncommitted. Final message = report.
`.agents/` read-only — read C-216 in `.agents/status/phase2-candidates.md`
first, then study `workctx agent install --agent all` in
`src/workctx/cli.py`, `AgentAdapterService.plan_install/install`, and
`ContextRegistry.list` (WP-750/WP-760 behavior, including register-on-use
and `WORKCTX_CONTEXT_REGISTRY`).

## Contract

One new command: `workctx agent refresh --all [--yes] [--agent <name|all>]`
(default `all`). Behavior, exactly as recorded in C-216:

- Iterate the machine context registry in stable ID order. `--all` is
  required; without it the command errors with guidance (future per-context
  refresh is out of scope).
- Skip entries whose root is missing or whose registered ID mismatches
  `context.yaml`, each with an explicit per-context warning; never guess.
- Per context, target only detected AVAILABLE clients (same rule and
  warning shape as `agent install --agent all`); honor `--agent` narrowing.
- Without `--yes`: fleet-wide dry run — plan every context, apply nothing.
  With `--yes`: apply per context. Nothing else auto-approves (D-045).
- One context's failure must not abort the batch: capture it, continue,
  and reflect it in the final summary plus a non-zero exit consistent with
  documented CLI exit bands. All-success exits 0.
- Output: human summary table with one row per context (context id,
  clients, refreshed / preserved-edits / merge-pending / skipped / failed)
  and a JSON envelope carrying per-context results including plan
  application state, merge_candidates, and skip/failure reasons.
- Reuse the existing planner and apply path verbatim. NO new mutation
  machinery, no adapter-service behavior changes. Orchestration may live in
  a new `src/workctx/adapters/agents/fleet.py` if that keeps `cli.py` thin.

## Allowed paths

`src/workctx/cli.py` (agent group + envelope helpers),
`src/workctx/adapters/agents/fleet.py` (new, optional),
`src/workctx/adapters/agents/__init__.py` (exports only),
`tests/cli/test_agent_refresh_cli.py` (new), `tests/agents_setup/**` (only
if a helper needs unit coverage), `docs/reference/cli-envelope.md` (rows),
`docs/reference/agent-adapters.md`, `docs/guides/multiple-contexts.md`
(short section). Nothing else. No schema, pyproject, or MCP changes.

## Tests required

Multi-context registry fixture (suite isolation is already global):
preview plans all and writes nothing; apply refreshes all; missing root
and mismatched ID are skipped with warnings while others proceed; an
injected per-context failure still processes the rest and yields the
documented non-zero exit; `--agent` narrowing; JSON payload shape pinned
(per-context results, merge_candidates passthrough); human table renders.
Run every gate you can; declare sandbox limits explicitly; existing tests
stay green.
