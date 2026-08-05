# Brief: WP-750 — Context inventory (`workctx context list`)

Codex worker, worktree `.worktrees/WP-750`, branch `agent/WP-750-context-list`.
You cannot commit; leave changes uncommitted. Final message = report.
`.agents/` is read-only for you.

## Problem (operator-reported, verified by the lead)

There is no way to see which contexts exist on this machine. The user-level
registry API already exists (`src/workctx/adapters/filesystem/registry.py`:
`ContextRegistry`, `list_contexts`, `RegisteredContext`) but NOTHING ever
registers a context: `list_contexts()` returns empty on a machine with real
contexts, and no CLI command exposes it.

## Scope

1. `initialize_context` registers the new context in the user registry
   (id + resolved root). Registration failure must NEVER fail context
   creation — swallow to a warning; the registry is advisory machine-local
   state, never canonical.
2. New `workctx context register [PATH]` (defaults to the resolved context)
   so contexts created before this change, or cloned from elsewhere, can be
   added. Idempotent; re-registering an existing id updates its root.
3. New `workctx context list [--json]`:
   - reads the registry, and for each entry reads `context.yaml` for id,
     name, kind, profile, language;
   - reports per context: id, name, kind, path, plus cheap stats — counts of
     tasks, entities, evidence notes, pending inbox artifacts, ledger event
     count, and last ledger activity timestamp;
   - marks entries whose `context.yaml` is gone as `missing: true` (never
     crash, never auto-delete the entry) and entries whose id no longer
     matches as `mismatched: true`;
   - stats must be CHEAP: prefer counting canonical files and reading the
     existing audit summary; do NOT rebuild projections and do NOT open a
     write lock. If a context is unreadable, report it with an error field
     rather than failing the whole listing.
   - human mode: one aligned table. `--json`: envelope-first per the CLI
     contract, rows in `docs/reference/cli-envelope.md`.
4. `workctx context unregister <context-id>` to drop a stale entry (registry
   only; never touches the context directory).
5. Document the group in `docs/reference/` where context commands live and
   add one row per command to the envelope table.

## Do NOT touch

Anything outside: `src/workctx/adapters/filesystem/registry.py` (additive
only), `src/workctx/services/contexts.py` (registration hook only),
`src/workctx/cli.py` (context group only), `tests/` files for these,
`docs/reference/cli-envelope.md` (rows), and the context reference doc. No
schema changes to `context.yaml`. No projection or lock usage in `list`.

## Tests required

Registration on init (and that a failing registry never breaks init);
register/unregister idempotency; list with zero, one, and several contexts;
missing and mismatched entries reported without crashing; stats correctness
on a fixture context; envelope validity and exit codes; no write lock taken
during list (assert by monkeypatching the lock to raise). Fictional data;
full gate; declare sandbox limits explicitly.
