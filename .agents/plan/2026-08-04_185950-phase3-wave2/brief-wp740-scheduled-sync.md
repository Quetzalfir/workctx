# Brief: WP-740 — Scheduled connector synchronization (no daemon)

Codex worker, worktree `.worktrees/WP-740`, branch `agent/WP-740-scheduled-sync`.
You cannot commit; leave changes uncommitted. Final message = report.
`.agents/` read-only — read C-214, D-047/D-049 in the decision register, and
docs/reference/connectors.md FIRST. Design constraint already decided by the
lead: NO resident daemon — scheduling belongs to the OS; workctx provides the
due-aware batch command the OS invokes.

## Scope

1. Manifest `schedule` vocabulary becomes real: `hourly | daily | weekly`
   (schema tightened from free metadata to this enum, still optional —
   absent means manual-only). Update connector-manifest schema + fixtures
   (ADR 0008 discipline).
2. Last-sync state: `98_state/connectors/last-sync.json` — machine-local
   advisory state (rebuild-safe, deletable): per connector+snapshot, the
   last successful sync UTC timestamp. Atomic replace on update; corrupt or
   missing state means "everything is due". Never canonical.
3. Engine additions in `src/workctx/connectors/`:
   - `sync_all(root, *, due_only=False, transport=None, clock=None)` ->
     typed per-connector results; continues past per-connector failures
     (one failing connector never blocks the rest; per-connector typed
     outcome), aggregate exit semantics defined below;
   - due logic: a snapshot is due when now - last_success >= its schedule
     interval (hourly=1h, daily=24h, weekly=7d), evaluated with the
     injected clock; manual `sync <name>` always runs regardless of
     schedule and also records last-sync.
4. CLI: extend the existing `connector` group: `connector sync --all
   [--due]` (mutually exclusive with a positional name), `connector status`
   (per connector+snapshot: schedule, last success, due-now boolean).
   Envelope rows in cli-envelope.md. Exit codes: all-succeeded 0; any
   connector failed -> 1 with per-connector errors in the envelope (partial
   results still reported).
5. docs/reference/connectors.md additions: the schedule vocabulary, due
   semantics, `connector status`, OS-scheduling recipes (Windows Task
   Scheduler command line and a crontab line invoking `workctx connector
   sync --all --due --json`), and a REAL worked GitHub read example
   manifest (api.github.com issues endpoint, secret_ref github-token,
   noting the GITHUB_TOKEN/gh fallback chain) marked as a real-world
   example — fictional hosts stay the rule for TESTS only.

## Do NOT touch

Anything outside: `src/workctx/connectors/**`, `src/workctx/cli.py`
(connector group only), `schemas/connector-manifest.schema.json` + fixtures,
`tests/connectors/**`, `tests/cli/test_connector_cli.py`,
`docs/reference/connectors.md`, `docs/reference/cli-envelope.md` (rows).
No daemon, no threads, no new dependencies, no outbox/send code.

## Tests required

Schedule enum schema fixtures; due math at exact boundaries with injected
clock; corrupt/missing last-sync state = all due, never an error; sync_all
continues past a failing connector (mocked transport) with correct
aggregate exit and per-connector outcomes; manual sync records last-sync;
`connector status` envelope; `--all` + name mutual exclusion usage error.
Mocked transport only (reuse the autouse no-real-HTTP guard). Full gate;
declare sandbox limits explicitly.
