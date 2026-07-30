# Worker report: `WP-100-reference-contracts`

## Status

`completed`

## Summary

Implemented the complete Wave 1 reference contract as typed domain code. Stable ID families,
canonical and source URIs, the D-018 entity vocabulary, typed relations, all nine source
locators, and Observation/Claim models now have deterministic validation and round-trip
behavior. The four owned JSON Schemas are aligned through shared positive and negative JSON
fixtures. The frozen `WorkctxUri` API remains available from its original import path through
a direct compatibility shim.

## Base and final commits

- Base: `ea6861f956326c35fbbca2bcaf276f423e08570f`
- Final implementation: `69d8c8697fe180aeec25b8b167fd6b04c585188f`

## Files changed

- Domain: `src/workctx/domain/{ids,references,locators,relations,vocabulary,observations,claims}.py`
- Compatibility: `src/workctx/models/reference.py`
- Schemas: `schemas/{reference,source-locator,observation,claim}.schema.json`
- Tests and shared fixtures: `tests/domain/**`
- Documentation: `docs/reference/reference-system.md`
- Reports: `.agents/work-orders/WP-100-reference-contracts/report.md` and `report.json`

## Behavior implemented

- Immutable parse/validate/format types for all 11 contracted ID families.
- The exact 19-value D-018 `EntityType` enum and schema-current relation, confidence,
  observation-kind, and claim-status vocabularies.
- `WorkctxUri` moved to the domain package without changing its public method signatures;
  the original model import is the same class object.
- Canonical observation URI normalization from literal `#OBS-NNN` to `%23OBS-NNN`, with
  actionable rejection from `WorkctxUri.parse`.
- Strict `artifact://sha256/<64-lowercase-hex>` and immutable
  `repo://<repo>@<commit>/<path>#L<start>-L<end>` reference types.
- Durable-reference rejection for absolute machine paths, traversal, malformed percent
  escapes, noncanonical known schemes, and `file://`.
- Nine discriminated Pydantic locator models, including range ordering, repository-path
  safety, normalized image bounds, and RFC 6901 JSON Pointer syntax.
- Typed reference, Observation, ObservationSource, and Claim models with extra-field
  rejection, schema-current enums, aware date-times, uniqueness, canonical URI validation,
  and JSON-valued claim objects.
- Shared JSON fixtures exercise schema -> model -> normal model dump -> schema round trips,
  plus parity negatives for every owned contract and Python-only semantic edge tests.

## Validation executed

| Command | Result | Notes |
| --- | --- | --- |
| `uv run ruff check .` | pass | `All checks passed!` |
| `uv run ruff format --check .` | pass | `155 files already formatted` |
| `uv run mypy src` | pass | `Success: no issues found in 21 source files` |
| `uv run pytest` | pass | `148 passed in 1.07s` |
| `uv run pytest tests/domain tests/test_reference.py tests/test_schemas.py` | pass | `133 passed in 0.40s` before the final full-suite run |
| `git diff --cached --check` | pass | No whitespace errors before the implementation commit |

## Assumptions and decisions

- ID date/year and sequence fields are lexical contracts only. Calendar semantics,
  nonzero-allocation rules, and next-free allocation are unspecified and remain out of scope.
- Repository commits follow the canonical source-locator schema: 7–64 hexadecimal characters,
  with input case preserved.
- The frozen `WorkctxUri` class remains structurally generic. D-018 membership is enforced by
  `EntityType` and by typed durable-reference/Claim boundaries, preserving existing callers.
- The schema-current 10 observation kinds and four claim statuses take precedence over the
  older, shorter prose lists in doc-03, as directed by the work-order context.
- External URI schemes remain syntax-only placeholders; no connector resolution was added.

## Contract deviations

- The branch copy of `contract.json` still contains `base_commit: PENDING-WAVE0-BASELINE`.
  The worker prompt and actual assigned base identify
  `ea6861f956326c35fbbca2bcaf276f423e08570f`. The contract file is forbidden, so it was not
  edited; this report records the actual base.

## Security and migration considerations

- Fixtures contain fictional data only and no credentials or private employer information.
- Entity IDs, Work Context URIs, repository references, and repository locators reject
  traversal and machine-specific durable paths after decoding.
- Public schemas now reject `file://`, unsafe repository paths, unknown entity types, and
  malformed known-scheme targets. This intentionally tightens inputs that were previously
  schema-valid but noncanonical or unsafe.
- Existing `workctx.models.reference.WorkctxUri` imports require no migration.

## Unresolved issues

None within WP-100 scope.

## Recommended next action

The implementation lead should independently inspect commit
`69d8c8697fe180aeec25b8b167fd6b04c585188f`, rerun the four validation commands, integrate
WP-100 before WP-110, and consolidate any duplicate Wave 1 ID/entity validators against these
canonical modules.
