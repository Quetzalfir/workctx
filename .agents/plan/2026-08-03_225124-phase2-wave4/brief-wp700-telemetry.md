# Brief: WP-700 — Usage telemetry and promotion/decay suggestions (C-212)

Codex worker, worktree `.worktrees/WP-700`, branch `agent/WP-700-telemetry`.
You cannot commit; leave changes uncommitted. Final message = report.
`.agents/` read-only — read the design doc
(.agents/plan/2026-08-03_200758-phase2-wave3/design-c202-c212.md), D-045, and
C-212 in phase2-candidates.md FIRST. WP-680's suggestion records are
integrated — your promotion/decay output feeds them.

## Non-negotiables (D-045)

Telemetry is OPT-IN per context and DEFAULT OFF. Zero recording, zero file
creation, zero overhead beyond one boolean check when disabled. Recording is
advisory machine-local state: `98_state/usage/usage.jsonl`, append-only,
size-rotated (default 5 MiB, keep 2), deletable at any time with zero data
loss. Lines carry timestamp, api name, and target URI or a SHA-256 of the
query text — NEVER file contents, bodies, or resolved secrets.

## Scope

1. `src/workctx/usage/`: opt-in detection (read `context.yaml` key
   `telemetry.usage: true` — if ContextConfig rejects unknown keys, STOP and
   report the exact rule), `record(root, api, target)` (best-effort: an
   unwritable usage file NEVER fails the read path — swallow to a one-time
   warning), rotation, `summarize(root, *, now)` folding into per-URI counts
   over 7/30/90-day windows, and promotion/decay evaluation per D-045
   thresholds (N=5/30d promotion, M=60d decay; overridable via
   `telemetry.promotion_uses`, `telemetry.decay_days`).
2. Instrumentation call sites (one line each, behind the boolean):
   projection `search`, retrieval `resolve`/`trace`/`build_pack`, and the
   MCP read tools (application layer). No other call sites.
3. Evaluation output: `evaluate_usage(root, *, now)` returns typed
   promotion/decay CANDIDATES; a `workctx usage` CLI group: `usage status`
   (enabled? file size? windows summary), `usage evaluate` (candidates,
   read-only), `usage suggest --yes` (creates WP-680 suggestion records from
   candidates via the approved-transaction API — one record per candidate,
   idempotent: an open record for the same target+kind is not duplicated).
   Envelope rows in cli-envelope.md.
4. `docs/reference/usage.md`: the privacy contract (what is recorded, what
   never is, how to delete), thresholds, and the suggestion flow.

## Do NOT touch

Anything outside: `src/workctx/usage/**`, the named instrumentation lines in
`src/workctx/adapters/sqlite/projection.py`, `src/workctx/retrieval/**`,
`src/workctx/mcp/application.py`, `src/workctx/cli.py` (usage group),
`tests/usage/**` (layout-guard-safe name), `docs/reference/usage.md`,
`docs/reference/cli-envelope.md` (rows). Suggestion records are consumed via
WP-680's public API only.

## Tests required

Disabled-by-default proof (no file, no overhead call); opt-in recording
shape; rotation; corrupt/unwritable usage file never breaks reads; window
math with injected clock; promotion and decay candidates at exact
thresholds; suggestion creation idempotency; CLI envelopes; secret/query
privacy (query hashed, never raw). Fictional data; full gate; declare
sandbox limits explicitly.
