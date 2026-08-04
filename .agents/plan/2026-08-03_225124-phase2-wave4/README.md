# Phase 2 — Wave 4 (operator-approved 2026-08-03; UI deferred by D-046)

Implements the C-202 adoption machinery and C-212 telemetry per
`../2026-08-03_200758-phase2-wave3/design-c202-c212.md` under the D-045
decisions (no auto-approve; telemetry opt-in default off; N=5/30d, M=60d
tunable; per-context overrides only).

## Packages

| Package | Scope | Worker | Launch |
| --- | --- | --- | --- |
| WP-680 | Suggestion records + data-fix adoption + view Records section | Codex (max) | immediately |
| WP-690 | Skill-override loader with three-way upgrade markers | Codex (max) | immediately (disjoint) |
| WP-700 | Usage telemetry seam + aggregator + promotion/decay suggestions | Codex (max) | after WP-680 integrates (emits into its record/suggestion plumbing) |

## Path ownership (disjoint for the parallel pair)

- WP-680: `src/workctx/suggestions/**` (new), `src/workctx/views/**`
  (Records section only), `src/workctx/cli.py` (suggestion group),
  `tests/suggestions/**` (LAYOUT GUARD: name must not shadow an importable
  module — "suggestions" is safe, verify against tests/test_layout.py),
  `tests/tasks_views/**` (view-section assertions), `schemas/**` (one new
  suggestion-record schema + fixtures per ADR 0008), `docs/reference/suggestions.md`.
- WP-690: `src/workctx/adapters/agents/**`, `tests/agents_setup/**`,
  `docs/guides/personalization.md` (overrides section).

## Wave-close criteria

1. Full gate + matrix green with all three integrated.
2. WP-680: a suggestion record is canonical, schema-validated, created and
   adopted ONLY via approved transactions; adoption of a data-fix applies its
   linked proposal atomically and supersedes the record; the suggestions view
   gains a Records section with ages.
3. WP-690: an adopted override under `06_overrides/skills/<name>/SKILL.md`
   merges over the packaged skill at render/install with provenance; a kit
   upgrade shows packaged-old vs packaged-new vs override (three-way marker);
   removing the override restores packaged behavior; overrides size-capped and
   secret-scanned like personalization layers.
4. WP-700 (second tranche): zero telemetry unless context.yaml opts in;
   usage.jsonl is machine-local, rotated, deletable with zero data loss;
   promotion/decay output is SUGGESTIONS only.
