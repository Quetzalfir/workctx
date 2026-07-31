# Worker report: `WP-230-context-packs`

## Status

`blocked`

## Summary

Implemented and validated deterministic projection-backed reference resolution, typed
graph traversal, exact source tracing, ranking, and budgeted ten-section context packs.
The implementation commit passes the complete repository gate with 462 tests, including
70 retrieval tests. Acceptance is blocked on an ADR 0008 contract-ownership decision:
the Pydantic model currently enforces relational invariants that standard JSON Schema
Draft 2020-12 cannot express, while ADR 0008 requires the hand-maintained schema and
Pydantic contract to reject every known divergence class consistently.

## Base and final commits

- Base: `0343911bfffe1faed359d743091f4e1e13039c22`
- Implementation: `3a1b65dc6ca32beb2fb95d155240541785cd540e`

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
- `tests/retrieval/fixtures/context-pack/negative/extra-field.json`
- `tests/retrieval/fixtures/context-pack/negative/fractional-budget.json`
- `tests/retrieval/fixtures/context-pack/negative/inconsistent-truncation.json`
- `tests/retrieval/fixtures/context-pack/negative/invalid-fingerprint.json`
- `tests/retrieval/fixtures/context-pack/negative/invalid-uri.json`
- `tests/retrieval/fixtures/context-pack/negative/missing-section.json`
- `tests/retrieval/fixtures/context-pack/negative/negative-budget.json`
- `tests/retrieval/fixtures/context-pack/negative/numeric-string-budget.json`
- `tests/retrieval/fixtures/context-pack/negative/wrong-schema-version.json`
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

- Added a frozen `ProjectionReader` protocol that consumes only WP-210 typed query APIs;
  retrieval contains no SQL, projection edits, canonical-file reads, embeddings, LLM
  calls, randomness, or network access.
- Added structural resolution for canonical `workctx://`, `artifact://`, and `repo://`
  references with explicit resolved/not-found results, D-018 type enforcement, and
  fail-closed context-boundary checks.
- Added deterministic breadth-first inbound/outbound/both traversal with depth and typed
  relation filters, lexical ordering, and cycle/duplicate suppression.
- Added current-by-default and history-on-request claim tracing through observations to
  immutable artifact references and exact typed locators. Missing, invalid, and foreign
  observation references produce stable guarded metadata rather than exception or source
  leakage.
- Added the documented eight-factor integer ranking function with weights, complete score
  tables, shared evidence reference time, literal Unicode-aware query token matching,
  full factor-vector tie-breaking, and per-factor tests.
- Added ten-section pack assembly with current claim bodies, compact history by default,
  historical evidence tracing, optional architecture context, deterministic sorted-key
  serialization, projection snapshot retry, and typed not-found/unsupported outcomes.
- Added deterministic `chars / 4` budget units, complete removal-tier and within-tier
  ordering, minimal focal preservation, explicit over-budget behavior, and complete
  omission metadata. Exact boundaries, Unicode rounding, repeated truncated output, and
  every tier are tested.
- Added recursive secret-pattern guarding, including digest-based synthetic IDs for
  invalid authored references, while retaining validated durable references and exact
  locator structure.
- Added the hand-maintained context-pack schema and ADR 0008 positive/negative fixtures,
  including schema/model round trips and rejection tests for expressible invalid shapes.

## Validation executed

| Command | Result | Notes |
| --- | --- | --- |
| `uv run ruff check .` | Passed | `All checks passed!` |
| `uv run ruff format --check .` | Passed | `242 files already formatted` |
| `uv run mypy src` | Passed | `Success: no issues found in 46 source files` |
| `uv run pytest` | Passed | `462 passed in 36.45s` |
| `uv run pytest tests/retrieval -q` | Passed | `70 passed in 12.39s` |
| `uv run pytest tests/test_plan_contracts.py -q` | Passed | `4 passed in 0.08s`; blocked report contract validated. |
| `git diff --cached --check` | Passed | Exit code 0 before implementation commit. |

## Assumptions and decisions

- Artifact and repository resolution is structural only because WP-210 exposes no
  artifact-manifest availability query; no availability claim is invented.
- Source quality uses observation kind and locator precision. Confidence remains its own
  factor, and missing WP-210 source-quality signals score zero.
- Reliable-evidence recency uses the first authored source observation that resolves; the
  same observation supplies timestamp, kind, and locator. Entity edit time and edge
  validity time are not substituted for evidence time.
- Pack assembly traces historical claim evidence even when full historical claim bodies
  are not requested, so compact superseding/contradictory metadata remains traceable.
- The projection has no distinct interaction record, so direct dated relationship
  observations are the available interaction evidence.
- Budget units count Unicode characters in compact sorted-key item JSON, round up after
  division by four, and exclude the stable envelope, wrappers, and truncation metadata.

## Contract deviations

- No implementation scope or allowed-path deviation.
- The frozen contract still records `status: proposed` and
  `base_commit: PENDING-WP210-INTEGRATION`. The direct assignment identified WP-210 base
  `0343911bfffe1faed359d743091f4e1e13039c22`; frozen contract files were not edited.
- ADR 0008 alignment is unresolved rather than silently weakened: the current schema
  accepts some relationally inconsistent instances that Pydantic rejects.

## Security and migration considerations

- All test data is fictional. Recursive guarding covers secret-named fields, credential
  assignments, bearer tokens, private-key blocks, nested JSON, and invalid authored
  reference metadata.
- Foreign `workctx://` references are refused before projection lookup. Related and traced
  local records remain inside the projection's bound context.
- Raw authored missing-reference text is never used as a synthetic item ID; a stable
  digest is used, and the guarded value remains only in metadata.
- Context-pack schema version 1 is new. This work changes no canonical workspace schema,
  projection schema, generated view, or migration path.

## Unresolved issues

- ADR 0008 says the hand-maintained JSON Schema is canonical and Pydantic implements the
  same contract. `ContextPack` rejects `focal_uri.context_id != context_id`, but standard
  JSON Schema Draft 2020-12 cannot compare two arbitrary instance strings.
- Pydantic enforces budget arithmetic, omitted-array/count correspondence, and per-section
  omitted counts. Standard Draft 2020-12 can express the existing zero/nonzero
  conditionals but not equality or arithmetic between arbitrary instance values.
- The documented rank total is a weighted sum of its factor vector. Enforcing that
  equality only in Pydantic would introduce another known schema/model divergence; leaving
  it producer-only needs an explicit contract decision.
- Resolving this safely requires the lead to choose whether these are producer/retrieval
  invariants outside the serialized schema contract, or to approve a representation or
  schema-validation redesign. Removing validators or changing the public representation
  without that decision would violate the worker stop conditions.

## Recommended next action

Implementation lead: review commit `3a1b65dc6ca32beb2fb95d155240541785cd540e`
and make an explicit ADR 0008 decision. The least disruptive option is to classify
cross-field context equality, exact budget/count arithmetic, and rank-total arithmetic as
producer/retrieval invariants; then align Pydantic to only the schema-expressible
conditionals and record that ownership decision in an allowed lead-owned ADR change.
Alternatively, approve a serialized representation or validator-vocabulary redesign that
can enforce the relations in the canonical contract. After that decision, return WP-230
for the bounded model/schema/test adjustment and acceptance review.
