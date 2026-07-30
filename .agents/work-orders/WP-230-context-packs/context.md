# Work-order context: WP-230-context-packs

## Why this exists

The decisive product scenario (doc-00) is answering "what is the current state of
TASK-X, what evidence supports it, who are we waiting on" from canonical state alone.
Resolution, traversal, tracing, ranking, and budgeted packs are that answer. This order
is sequential after WP-210 because it consumes the projection query APIs.

## Required architecture and decisions

- doc-03 sections: Context packs (10-point structure), ranking factors, and Required CLI
  and MCP behaviors — you implement the behaviors as APIs; commands come later.
- ADR 0008: context-pack.schema.json is hand-maintained with positive/negative fixtures.
- D-018 vocabulary and the frozen WorkctxUri API govern reference handling.

## Existing implementation

- WP-210 (integrated on your base): typed query APIs and docs/reference/projections.md —
  your data plane. Read that doc first.
- Domain: Observation/Claim/TypedReference/RelationType models, locators, ids,
  normalize_workctx_uri — all in `workctx.domain`.
- WP-100's trace primitives: ArtifactReference/RepoReference parse+format.
- tests/workspace and tests/projections fixtures show realistic document shapes.

## Dependencies

- WP-200/WP-220 are integrated by the time you start; nothing of yours overlaps them.
- If a ranking factor needs data the projection lacks (e.g. source quality signals),
  raise a coordination request to the lead — do not edit the adapter or degrade the
  factor silently; document any lead-approved simplification in your report.

## Known risks and edge cases

- Token budgets: define budget units precisely (approximate tokens = chars/4 is
  acceptable if documented and deterministic); truncation order must be stable across
  runs.
- Superseded chains can be long; packs include the current claim plus chain summary,
  not every historical claim body, unless history is explicitly requested.
- One-hop architecture entities (doc-03 point 9) are optional — include behind a flag.
- Empty results (entity with no relations) must produce a valid, minimal pack, not an
  error.
- New test directory `tests/retrieval/` needs an `__init__.py`.
