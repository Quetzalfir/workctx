# Brief: WP-510 — Legacy migration engine and CLI

Codex worker, worktree `.worktrees/WP-510-migration`, branch `agent/WP-510-migration`.
You cannot commit; leave changes uncommitted. Final message = report. `.agents/` is
read-only for you.

## Mission

Implement `workctx migrate legacy <source-path> <target-context-path>` with
`--dry-run` (default) and `--apply`, per `.agents/plan/initial/10-migration-from-legacy-repo.md`.
The migration converts a legacy Markdown work repository into a valid workctx
context. Deterministic code only — no LLM calls.

## Scope

1. New package `src/workctx/migration/` (engine independent of CLI):
   - Stage pipeline per doc-10 §Migration stages (13 stages). Dry-run executes
     stages 1-3 (+ mapping preview) and writes NOTHING outside the report.
   - Inventory + classification (canonical vs generated vs obsolete vs unknown).
   - Detection pass: secrets (`workctx.validation.engine.contains_possible_secret`),
     absolute paths, duplicate IDs, broken links, unknown entity types.
   - Mapping to the context template (initialize_context on a NEW empty target;
     refuse a non-empty target).
   - Frontmatter normalization via existing domain models — meaning-preserving only.
   - Artifact manifests for preserved originals via `workctx.ingestion` when raw
     evidence exists; explicit `raw_unavailable` provenance marker when only derived
     notes exist (doc-10 §Missing original evidence).
   - Claims for mutable state; observations only where a source locator is
     recoverable — never fabricate locators.
   - Migration report (JSON + Markdown): every loss of precision, every skipped
     file with reason, old-path → new-URI table (the migration ledger).
   - Final steps: run validation engine on the staged context, rebuild projections
     and views, verify the SOURCE tree byte-identical (hash before/after).
2. CLI group `migrate` in `src/workctx/cli.py` (envelope-first, lazy imports,
   `--json`, exit codes per D-015); row in `docs/reference/cli-envelope.md`.
3. `docs/reference/migration.md` — behavior, stages, report format, limitations.
4. Fictional sanitized legacy fixture under `tests/migration/fixtures/legacy-repo/`
   exercising: nested tasks, duplicate IDs, an absolute path, a fake secret, a
   derived-only evidence note, an unknown entity type. 100% fictional content.

## Decision you must NOT make alone

How `--apply` interacts with the audit ledger (per-entity transaction events vs a
single import event vs no events). Implement behind a small seam, pick ONE as
default, and flag it prominently in your report as a decision request with
trade-offs. Do not touch `src/workctx/transactions/` internals.

## Do NOT touch

Anything outside: `src/workctx/migration/**`, `tests/migration/**`,
`src/workctx/cli.py` (migrate group + its lazy import only),
`docs/reference/migration.md`, `docs/reference/cli-envelope.md` (one table row).
Engines are consumed as-is; gaps = blocker report.

## Tests required

Dry-run writes nothing; apply produces a valid context (validation engine clean);
source untouched (hash check); secret detected → migration REFUSES apply until
`--allow-findings` (report-only override that still never copies the secret value);
duplicate-ID and broken-link handling; derived-only evidence marked
`raw_unavailable`; report completeness. Full gate must pass:
`uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`,
`uv run pytest` (coverage floor 82 stands).
