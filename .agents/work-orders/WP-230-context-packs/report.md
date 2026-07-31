# Worker report: `WP-230-context-packs`

## Status

`completed`

## Summary

Implemented deterministic projection-backed reference resolution, typed graph traversal,
exact source tracing, explainable ranking, and budgeted ten-section context packs. The
ADR 0011 revision separates structural negative fixtures from schema-valid
producer-invariant fixtures, discloses the validation boundary in the schema and reference
documentation, and records `rank.total` as a producer-derived value that is authoritative
for consumers. The branch includes master `22694e9`; the final combined gate passes with
707 tests, including 78 retrieval tests.

## Base and final commits

- Pinned base: `0343911bfffe1faed359d743091f4e1e13039c22`
- Original implementation: `3a1b65dc6ca32beb2fb95d155240541785cd540e`
- Original blocker report: `a4ca1008a911734f7d4218aa690ec4ce25b5152b`
- Lead decision/master dependency: `22694e9de23ba0fb9691a2f5ebba0499c66fac2d`
- Master merge: `009bb7843263c90a7776e1d89a1a01489c25b769`
- Final implementation revision: `bc80d47b6799febd61a7cc29f363100bfef71f68`

## Files changed

- `docs/reference/context-packs.md`
- `schemas/context-pack.schema.json`
- `src/workctx/retrieval/__init__.py`
- `src/workctx/retrieval/builder.py`
- `src/workctx/retrieval/graph.py`
- `src/workctx/retrieval/models.py`
- `src/workctx/retrieval/protocols.py`
- `src/workctx/retrieval/ranking.py`
- `src/workctx/retrieval/records.py`
- `src/workctx/retrieval/references.py`
- `src/workctx/retrieval/security.py`
- `src/workctx/retrieval/serialization.py`
- `src/workctx/retrieval/tracing.py`
- `tests/retrieval/__init__.py`
- `tests/retrieval/fixtures/context-pack/negative/producer-invariant/focal-context-mismatch.json`
- `tests/retrieval/fixtures/context-pack/negative/producer-invariant/minimum-exceeds-used.json`
- `tests/retrieval/fixtures/context-pack/negative/producer-invariant/omitted-count-mismatch.json`
- `tests/retrieval/fixtures/context-pack/negative/producer-invariant/over-budget-arithmetic-mismatch.json`
- `tests/retrieval/fixtures/context-pack/negative/producer-invariant/section-omission-mismatch.json`
- `tests/retrieval/fixtures/context-pack/negative/producer-invariant/within-budget-mismatch.json`
- `tests/retrieval/fixtures/context-pack/negative/structural/extra-field.json`
- `tests/retrieval/fixtures/context-pack/negative/structural/fractional-budget.json`
- `tests/retrieval/fixtures/context-pack/negative/structural/inconsistent-truncation.json`
- `tests/retrieval/fixtures/context-pack/negative/structural/invalid-fingerprint.json`
- `tests/retrieval/fixtures/context-pack/negative/structural/invalid-uri.json`
- `tests/retrieval/fixtures/context-pack/negative/structural/missing-section.json`
- `tests/retrieval/fixtures/context-pack/negative/structural/negative-budget.json`
- `tests/retrieval/fixtures/context-pack/negative/structural/numeric-string-budget.json`
- `tests/retrieval/fixtures/context-pack/negative/structural/wrong-schema-version.json`
- `tests/retrieval/fixtures/context-pack/positive/complete.json`
- `tests/retrieval/fixtures/context-pack/positive/minimal.json`
- `tests/retrieval/support.py`
- `tests/retrieval/test_builder.py`
- `tests/retrieval/test_context_pack_contract.py`
- `tests/retrieval/test_graph_and_tracing.py`
- `tests/retrieval/test_ranking.py`
- `tests/retrieval/test_references.py`
- `tests/retrieval/test_security.py`
- `.agents/work-orders/WP-230-context-packs/report.md`
- `.agents/work-orders/WP-230-context-packs/report.json`

## Behavior implemented

- Added a frozen `ProjectionReader` protocol consuming only WP-210 typed queries; retrieval
  contains no SQL, adapter mutation, canonical-file reads, embeddings, LLM calls,
  randomness, or network access.
- Added structural `workctx://`, `artifact://`, and `repo://` resolution with explicit
  resolved/not-found results, D-018 type enforcement, and fail-closed context boundaries.
- Added deterministic breadth-first typed traversal with direction, depth, relation
  filtering, lexical ordering, and cycle/duplicate suppression.
- Added current-by-default and history-on-request tracing from tasks and claims through
  exact observations to immutable source references and typed locators.
- Added the documented eight-factor integer ranking function with full score tables,
  reliable-evidence timestamps, literal Unicode-aware query matching, and stable total,
  factor-vector, and key tie-breaking.
- Added deterministic ten-section pack assembly with compact history, exact historical
  evidence tracing, optional architecture context, projection-snapshot retry, stable JSON,
  and typed missing/unsupported outcomes.
- Added documented Unicode-character budget units, complete removal-tier and within-tier
  ordering, minimal focal preservation, explicit overage, and omission metadata.
- Added recursive secret-pattern guarding and digest-based synthetic IDs for invalid
  authored references while preserving validated durable references and typed locators.
- Added the hand-maintained schema, positive fixtures, structural both-reject negatives,
  and schema-valid/Pydantic-rejected producer-invariant negatives under ADR 0008/0011.
- Corrected the complete positive example's weighted rank total to 9,625 and tested both
  producer calculation and authoritative consumer preservation.

## Validation executed

| Command | Result | Notes |
| --- | --- | --- |
| `uv run ruff check .` | Passed | `All checks passed!` |
| `uv run ruff format --check .` | Passed | `276 files already formatted` |
| `uv run mypy src` | Passed | `Success: no issues found in 58 source files` |
| `uv run pytest` | Passed | `707 passed in 83.79s` |
| `uv run pytest tests/retrieval -q` | Passed | `78 passed in 13.52s` |
| `uv run pytest tests/retrieval/test_context_pack_contract.py tests/retrieval/test_ranking.py -q` | Passed | `48 passed in 0.41s` |
| `uv run pytest tests/test_plan_contracts.py -q` | Passed | `4 passed in 0.10s`; completed report contract validated. |
| `git diff --cached --check` | Passed | Exit code 0 before the revision implementation commit. |

## Assumptions and decisions

- Artifact and repository resolution is structural because WP-210 exposes no
  artifact-manifest availability query.
- Source quality uses observation kind and locator precision; confidence remains an
  independent factor and unavailable signals score zero.
- The first authored source observation that resolves supplies timestamp, kind, and
  locator for a claim or edge.
- Historical evidence remains traced even when full historical claim bodies are omitted.
- Direct dated relationship observations are the available interaction evidence because
  the projection has no distinct interaction record.
- Budget units count Unicode characters in compact sorted-key item JSON and exclude
  envelope, wrapper, and truncation-metadata costs.
- Structural constraints use standard Draft 2020-12 representations and are rejected by
  both schema and model. Producer-invariant fixture instances remain schema-valid and are
  rejected by Pydantic.
- Ranking producers compute the weighted total. Once serialized, the in-range total is
  authoritative for ordering; schema and Pydantic consumers do not recompute it.

## ADR 0011 standard-representation analysis

| Constraint | Standard representation considered | Outcome |
| --- | --- | --- |
| `focal_uri` context equals `context_id` | Remove the duplicated context or serialize a context-relative focal reference. | Either loses a self-contained durable URI or removes public metadata; both are public representation redesigns, so the current relation is a producer invariant. |
| `minimum_units <= used_units`, within-budget truth, and exact overage | Remove `within_budget`/`over_budget_by`, or replace the object with disjoint tagged shapes. | Draft 2020-12 still cannot compare arbitrary numeric siblings or calculate the exact difference; removing fields is a public contract change. |
| Global omission count equals array length | Remove `omitted_count` and require consumers to derive it. | This is expressible only by deleting the redundant public field; that is not a bounded correction. |
| Per-section omission counts and their total | Partition omission arrays inside each section or remove per-section counts. | Both alter the ten-section public representation; Draft 2020-12 cannot count array members filtered by `section`. |
| Exact included-item, compact-focal, and omission-unit accounting | Omit derived unit metadata or serialize removed item bodies so consumers can recalculate. | Omitting metadata weakens the truncation explanation; including removed bodies defeats the budget. Producer code must calculate these values. |
| Weighted `rank.total` | Remove either total or the explanatory factor vector, or enumerate all factor combinations. | Removing either field changes the explainable ranking contract; exhaustive enumeration is unreasonable. Producers calculate the formula and consumers treat the serialized total as authoritative. |

The schema already uses reasonable standard representations for expressible correlations:
`if`/`then` constrains truncation against zero/nonzero omission metadata and
within-budget state against zero/positive overage; exactly one focal item uses
`minItems`/`maxItems`; standard keywords cover types, bounds, enums, required fields, URI
shape, and additional-property rejection. No producer-invariant classification replaces
a reasonable standard representation.

## Contract deviations

- No implementation scope or allowed-path deviation.
- The branch merged the lead-requested master commit `22694e9`; no master-owned file was
  manually changed by this worker.

## Security and migration considerations

- All fixtures are fictional. Recursive guarding covers secret-named fields, credential
  assignments, bearer tokens, private-key blocks, nested JSON, and invalid authored
  reference metadata.
- Foreign `workctx://` references are refused before projection lookup, and local
  retrieval remains inside the projection's bound context.
- Raw missing-reference text never becomes a synthetic item ID; a stable digest is used.
- Context-pack schema version 1 is new and changes no canonical workspace schema,
  projection schema, generated view, or migration path.

## Unresolved issues

None.

## Recommended next action

Implementation lead: inspect revision `bc80d47b6799febd61a7cc29f363100bfef71f68`,
validate this completed report, run the final acceptance gate, and integrate WP-230 before
wiring the lead-owned ref/context-pack CLI commands.
