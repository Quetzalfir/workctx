# Brief: WP-520 — Alpha acceptance scenarios

Codex worker, worktree `.worktrees/WP-520-acceptance`, branch
`agent/WP-520-acceptance`. You cannot commit; leave changes uncommitted. Final
message = report. `.agents/` is read-only for you.

## Mission

Automate the full-product-cycle smoke the lead ran manually at Wave 4 close as
durable acceptance tests under `tests/e2e/`, driving the REAL public surfaces
(CLI envelopes via CliRunner and public service APIs), never private helpers.

## Scenarios (each an independent test module, fictional data only)

1. `test_full_cycle.py` — the doc-00 decisive scenario end to end:
   register raw note (`inbox add`) → `begin_processing` → `stage_observations`
   (payload shaped like `tests/evidence/support.py::_payload`) →
   `build_evidence_proposal` → `apply(approved=True)` → `complete_processing`
   archives under artifact identity → evidence-backed task via TaskService
   (assert the evidence-required REFUSAL first — it is a product invariant) →
   `rebuild_views` (brief.md, waiting-on.md content asserts) → `save_draft` to
   05_outbox with UNCERTAIN marker and source_refs → `verify_ledger` (event count
   and chain). Assert URIs resolve via `ref show`/`ref trace` CLI.
2. `test_quarantine_cycle.py` — prompt-injection artifact quarantines, never
   reaches 01_processed, its bytes never appear in any envelope, report, view, or
   proposal; quarantined list/reasons via CLI.
3. `test_approval_gates.py` — every mutation path refuses without
   `approved=True` / `--approve`: transaction apply, draft save, task mutation,
   MCP mutation tools (in-process server, `approved: true` flag per ADR 0012).
4. `test_rebuild_projections.py` — delete SQLite projection + 04_views entirely,
   rebuild from canonical Markdown/YAML, assert search/context-pack/views
   equivalence (projections are disposable; canonical is truth).
5. `test_cross_surface_consistency.py` — same question answered via CLI search,
   context-pack, and MCP read tool returns consistent references.

## Rules

- `tests/e2e/**` is your ONLY writable path. A product defect is a BLOCKER report
  with a minimal repro, never a src edit or a test that asserts the buggy behavior.
- Deterministic: injected clocks where APIs accept them; no sleeps; no network.
- Keep runtime sane (< ~60s locally for the whole package); reuse
  session-scoped fixtures where safe.
- Full gate must pass: ruff check, ruff format --check, mypy src, pytest
  (coverage floor 82 stands — your tests raise coverage, never lower it).
