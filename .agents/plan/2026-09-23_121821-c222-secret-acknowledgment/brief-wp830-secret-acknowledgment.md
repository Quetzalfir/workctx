# Brief: WP-830 — Locatable secret findings and acknowledged overrides (C-222)

Codex worker, worktree `.worktrees/WP-830`, branch `agent/WP-830-secret-acknowledgment`.
No commits; final message = report. `.agents/` read-only. Read C-222 in
`.agents/status/phase2-candidates.md` first, then study the detector
(`_contains_possible_secret` and callers in `src/workctx/validation/
engine.py`), the WP-810 strict-scope seam in the transaction engine, the
ledger event shape, and the WP-810 `META-SCHEMA-STALE` +
`DIAGNOSTIC_DEFINITIONS` pattern.

## Contract

1. Locatable findings. CTX-POSSIBLE-SECRET issues carry, in the issue
   message (and any structured detail the report model already allows):
   the 1-based line number and the pattern kind — `private-key marker`,
   `bearer-token marker`, or `assignment to key '<key name>'`. The key
   NAME may be shown; matched VALUE bytes must never appear in any
   message, diagnostic, log, or JSON payload (extend the existing
   value-free discipline; test this negatively). Multiple findings in
   one file are all reported. The ingestion-guard predicate
   (`contains_possible_secret`) keeps its public boolean contract.
2. Durable operator acknowledgment.
   - New canonical file `99_meta/secret-scan-acknowledgments.yaml`
     (schema_version 1): entries of context-relative path, sha256
     content hash, UTC acknowledged_at, optional note (<=500 chars).
     JSON Schema added under `schemas/`. Never created empty.
   - `workctx transaction apply --yes --acknowledge-possible-secret
     <path>` (repeatable; also on dry-run for preview) is valid only for
     paths the transaction writes whose staged content actually fires
     the detector; anything else is a typed error. The apply stages the
     acknowledgment entry (add or update) INSIDE the same transaction,
     and the ledger event lists the acknowledged paths.
   - Validation (workspace + post-commit): a CTX-POSSIBLE-SECRET finding
     whose path has an acknowledgment with a MATCHING content hash
     downgrades to advisory severity, message noting the acknowledgment;
     hash mismatch or missing entry keeps the error and says the
     acknowledgment is stale. `context validate` therefore returns ok
     for acknowledged-and-unchanged files.
   - MCP `transaction_apply` gains the same optional argument with
     identical semantics; the approval gate is unchanged.
3. Discovery. One sentence extending the existing bridge escape-hatch
   text and the `workctx guide` never-edit/escape section: on
   CTX-POSSIBLE-SECRET, first locate via the reported line and pattern
   and REDACT or replace with a secret reference name; only when the
   operator explicitly confirms the text is not a live credential,
   re-apply with `--acknowledge-possible-secret`; never acknowledge
   merely to clear an error. Equivalent short mention in the
   process-evidence and curate-knowledge skills and the context template
   AGENTS.md bullet list. Docs: validation-diagnostics.md row repair
   action updated; secrets.md short section; cli-envelope.md rows.

## Allowed paths

`src/workctx/validation/**`, `src/workctx/transactions/**`,
`src/workctx/mcp/application.py`, `src/workctx/cli.py`,
`src/workctx/guide.py`, `schemas/` (new acknowledgment schema + ledger
event field if required), agent kit bridges (3) + packaged
`process-evidence`/`curate-knowledge` SKILL.md, canonical context
template AGENTS.md via `scripts/sync_context_template.py`,
`docs/reference/{validation-diagnostics,secrets,cli-envelope}.md`,
`tests/validation/**`, `tests/validation_engine/**`,
`tests/transactions/**`, `tests/cli/**`, `tests/mcp/**`,
`tests/agents_setup/**` (content assertions), `tests/test_skills.py`.
NOTE: `.agents/skills` mirrors and the template AGENTS.md hash history
(`_HISTORICAL_TEMPLATE_BRIDGE_HASHES`) are lead-reconciled after
delivery — do not touch `.agents/` or that constant.

## Tests required

Multi-finding location reporting with exact lines and kinds; negative
leak test (a fictional value never appears in any output); acknowledgment
lifecycle end to end (blocked apply -> acknowledged apply succeeds ->
validate ok -> file edited -> error returns naming staleness); flag
rejected for paths without findings and for unwritten paths; ledger
event content; MCP parity; bridge/guide/skill content assertions. Full
gate where the sandbox allows; declare limits explicitly; existing tests
stay green.

## Amendment 1 (lead, 2026-09-23)

Worker blocker accepted. Two files are ADDED to the allowed paths, each
for one additive change only:
- `src/workctx/domain/transactions.py`: `AuditEventContent` gains an
  OPTIONAL acknowledged-paths field (default empty); existing ledger
  events remain valid and hash verification of prior chains is
  untouched.
- `src/workctx/mcp/contracts.py`: the `transaction_apply` input schema
  gains the same OPTIONAL argument — a backward-compatible extension of
  the version 1 tool surface per ADR 0012. No other contract changes.
