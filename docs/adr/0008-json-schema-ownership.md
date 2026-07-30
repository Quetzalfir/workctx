# ADR 0008: JSON Schema ownership and Pydantic alignment

- Status: accepted
- Date: 2026-07-30

## Context

External boundaries require explicit schemas. The repository ships hand-written JSON Schemas
under `schemas/` while runtime validation uses Pydantic models. The architecture plan asks
whether JSON Schema files are generated from Pydantic or jointly maintained with contract
tests.

## Decision

- `schemas/` is the canonical, hand-maintained public contract, versioned independently of
  implementation details.
- Pydantic models implement the same contracts; alignment is enforced by contract tests:
  shared example fixtures must validate against the JSON Schema (Draft 2020-12) and
  round-trip through the corresponding Pydantic model.
- Positive fixtures alone cannot catch looseness drift (a schema stricter than the model,
  or vice versa, outside the fixture set). A live example existed at the time of writing:
  `context.schema.json` pinned `schema_version` to `const: 1` while the model accepted
  `ge=1`. Therefore every contract additionally requires **negative fixtures**: for each
  known divergence class, at least one instance that must be rejected by BOTH schema and
  model. A rejection accepted by either side fails the suite.
- Automatic generation from Pydantic is rejected for Phase 1: generated output couples the
  public contract to Pydantic's schema dialect and internal representation choices, and
  makes contract changes invisible in review diffs.
- Every contract change must touch schema, model, and fixtures in the same change set; the
  fixture suite (positive round-trip plus negative rejections) is the drift tripwire — it
  narrows, but does not eliminate, undetected divergence outside the fixture space.
- Revisit after the first alpha if drift maintenance proves more costly than the coupling.

## Consequences

- schema diffs remain reviewable and intentional;
- double maintenance is the accepted cost, bounded by contract tests that fail on drift;
- fixtures become load-bearing artifacts and belong in `tests/` with the same review rigor
  as code;
- MCP and CLI envelope schemas follow the same rule when they stabilize.
