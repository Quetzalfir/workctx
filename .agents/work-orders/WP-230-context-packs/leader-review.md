# Leader review: `WP-230-context-packs`

## Decision

`accepted` (after one revision round)

Round 1 (`revision_requested`): the worker blocked correctly on ADR 0008's both-reject
rule versus relations Draft 2020-12 cannot express. Lead resolution recorded as ADR 0011
(structural vs producer-invariant tiers); a six-point bounded correction was returned on
the same branch. Round 2: correction delivered and accepted.

## Contract compliance

- Base `0343911` (pinned contract base); blocker delivery `3a1b65d`/`a4ca100`; correction
  `bc80d47`, final report `0f5059a`. The worker merged the lead's `22694e9` (ADR 0011)
  before correcting — post-merge correction diff verified to touch ONLY the bounded
  scope: fixtures, contract tests, schema description, context-packs doc, reports.
- Full-path audit across both rounds: all changes inside `allowed_paths`; no adapter,
  domain, presentation, or foreign-schema edits.

## Diff review

- Retrieval package: projection-backed resolution, typed traversal with depth control,
  claim→observation→locator tracing, explainable deterministic ranking (per-factor unit
  tests), budgeted ten-section packs with truncation metadata and pack fingerprints.
- ADR 0011 applied precisely: 9 structural negative fixtures (rejected by schema AND
  model) + 6 producer-invariant fixtures (model rejection asserted; schema acceptance
  asserted — documenting the boundary); the schema's top-level description enumerates
  every producer invariant; docs/reference/context-packs.md carries the same list with
  the rank-total classified as a producer invariant (serialized value authoritative).
- Per-constraint expressibility check documented in the report (no reasonable standard
  representation exists for the six model-only relations — verified reasoning).

## Validation executed

| Command | Result | Notes |
| --- | --- | --- |
| report.json vs agent-report.schema.json | pass | validated by lead |
| `uv run ruff check .` | pass | worker worktree, independent run |
| `uv run ruff format --check .` | pass | 276 files |
| `uv run mypy src` | pass | 58 source files, strict |
| `uv run pytest` | pass | 707 passed (branch includes merged master) |

## Findings

- The blocker-then-revision loop worked exactly as the orchestration protocol intends:
  no silent weakening, a recorded architecture decision, and a bounded correction.

## Required revisions

None outstanding.

## Integration notes

- Integrated as the final Wave 2 order. Remaining lead work at wave close: wire
  ref show/related/trace and context-pack CLI commands over the retrieval APIs, then run
  the wave-close combined gate.
