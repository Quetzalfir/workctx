# Brief: WP-680 — Suggestion records and data-fix adoption

Codex worker, worktree `.worktrees/WP-680`, branch `agent/WP-680-suggestions`.
You cannot commit; leave changes uncommitted. Final message = report.
`.agents/` read-only — read the design doc
(.agents/plan/2026-08-03_200758-phase2-wave3/design-c202-c212.md), D-045 in the
decision register, and C-202 in phase2-candidates.md FIRST. The suggestions
VIEW (detection) already exists; you are building the RECORD lifecycle.

## Scope

1. New package `src/workctx/suggestions/`:
   - `SuggestionRecord` model: id grammar `SUG-<YYYYMMDD>-<slug>-<nn>`, type
     (data_fix | skill_override | engine_proposal), status (open | adopted |
     rejected | superseded), rationale, signal, source_refs (evidence/URIs),
     and for data_fix an embedded, fully validated TransactionProposal;
   - canonical location `03_work/suggestions/<id>.md` (frontmatter + body),
     hand-maintained JSON Schema in `schemas/` with positive/negative
     fixtures (ADR 0008 discipline);
   - `create_suggestion(root, payload, approved=...)` and
     `adopt_suggestion(root, suggestion_id, approved=...)` — BOTH are
     ordinary approved transactions (D-045: nothing auto-approves).
     Adoption of data_fix applies the embedded proposal and marks the record
     adopted in ONE atomic apply (multi-target transaction); skill_override
     and engine_proposal adoption only flips status in v1 (their machinery
     is WP-690/manual);
   - `reject_suggestion` and supersession semantics (history preserved).
2. Suggestions view gains a "Records" section: open records with type, age,
   one-line rationale, URI. (Records section ONLY — do not touch the five
   detection signals.)
3. CLI group `suggestion` in cli.py: `suggestion list/show/adopt/reject`
   (envelope-first, adopt/reject require `--yes`), rows in cli-envelope.md.
4. `docs/reference/suggestions.md`.

## Do NOT touch

Anything outside: `src/workctx/suggestions/**`, `src/workctx/views/**`
(Records section), `src/workctx/cli.py` (suggestion group), `schemas/**`
(new schema + fixtures only), `tests/suggestions/**`, `tests/tasks_views/**`,
`docs/reference/suggestions.md`, `docs/reference/cli-envelope.md` (rows).
Engines consumed as-is; gaps = blocker.

## Tests required

Record schema positive/negative fixtures; create/adopt/reject via
transactions with approval refusals; atomic data-fix adoption (proposal
applied + record adopted in one ledger event, rollback leaves neither);
supersession history; Records view section determinism and ages; CLI
envelopes and exit codes. Fictional data; full gate; declare sandbox limits.
