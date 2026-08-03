# Brief: LEAD-W3 — Inbox/artifact CLI commands

Worktree `.worktrees/LEAD-W3-inbox-cli`, branch `lead/inbox-cli`. You cannot commit;
leave changes uncommitted. Final message = report.

## Scope (exactly four commands; templates: `ref show` cli.py:304, `index rebuild`)

1. `inbox add <files...> [--source S] [--event-date D] [--json]` →
   `workctx.ingestion.IngestionService.register` per file; result lists per-file
   outcome (registered id / duplicate / quarantined) — a quarantine outcome is a
   SUCCESS envelope with the outcome reported, not an error.
2. `inbox list [--status pending] [--json]` → ingestion list API.
3. `artifact show <artifact-id-or-uri> [--json]` → manifest lookup (ingestion API;
   accepts ART id or artifact:// URI via domain parsers).
4. `artifact verify <artifact-id-or-uri> [--json]` → recompute streaming hash vs
   manifest; report match/mismatch (mismatch = ok:false, user-correctable).

`--context` everywhere; envelope-first; lazy imports; update the command table in
`docs/reference/cli-envelope.md`.

## Do NOT touch

Anything outside: `src/workctx/cli.py`, `tests/cli/test_inbox_cli.py` (new),
`docs/reference/cli-envelope.md`. Engines are consumed as-is; gaps = report a blocker.

## Tests required

Envelope validity, exit codes, stdout purity (split streams), duplicate and
quarantine outcomes, verify mismatch after tampering a raw file. Full gate must pass.
