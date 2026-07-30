# Work-order context: WP-100-reference-contracts

## Why this exists

The reference model (doc-03) is the foundation for traceability: stable IDs, canonical URIs,
source locators, and typed relations. The scaffold implements only the `workctx://` URI type;
everything else exists solely as JSON Schemas with no Python counterpart. WP-200, WP-210,
WP-230, and WP-300 are blocked on these contracts.

## Required architecture and decisions

- `.agents/plan/initial/03-reference-and-retrieval-model.md` — the semantics this package owns.
- `.agents/plan/initial/02-architecture.md` — domain code must not import Typer, Rich, MCP,
  SQLite, or agent config modules.
- ADR 0008 — `schemas/` is hand-maintained canonical; alignment via shared fixtures.
- Lead decisions D-006/D-007 (see `.agents/status/decision-register.md`): `models/__init__.py`
  and `domain/__init__.py` are frozen; `WorkctxUri` public API is frozen during Wave 1.

## Existing implementation

- `src/workctx/models/reference.py` — `WorkctxUri` frozen dataclass: parse/str round-trip,
  lowercase-hyphen validation, `%23` encoding for observation fragments (str encodes with
  `quote(safe='-._~')`), traversal rejection (decodes before checking `..`), and
  `require_context` boundary enforcement. 4 tests in `tests/test_reference.py`.
- `schemas/reference.schema.json` — full 22-relation vocabulary, confidence, validity fields.
- `schemas/source-locator.schema.json` — all 9 locator types as `oneOf`; `repo_range` requires
  a 7-64 hex commit; `whole_artifact` requires a justification string.
- `schemas/observation.schema.json` — `^EVD-.+#OBS-[0-9]{3}$` IDs; kind enum with 10 values
  (extends doc-03's list with task/blocker/dependency); `artifact://sha256/` source refs.
- `schemas/claim.schema.json` — `CLM-[0-9]{4}-[0-9]{5}`; status enum adds `retracted` and
  `uncertain` beyond doc-03. Treat the schemas as the more current vocabulary.
- Only `models/__init__.py` and `tests/test_reference.py` import `models/reference.py`, so
  the move-to-domain refactor is low-risk if the shim keeps import paths stable.

## Dependencies

- WP-001 baseline: green gate, `src/workctx/domain/__init__.py` exists (do not edit it).
- WP-110 runs in parallel and imports `WorkctxUri` through the existing stable path
  `workctx.models.reference` — that is why the shim and API freeze are contractual.

## Known risks and edge cases

- `urlparse` treats a literal `#` as a fragment: only `%23`-encoded observation URIs parse.
  This is correct behavior per the frozen API; your job is to document it, add a
  normalization helper, and test both directions.
- The entity-type vocabulary anchor is decision D-018 in `.agents/status/decision-register.md`
  — a fixed 19-value list. Test against that literal list, never against WP-110's in-flight
  `entity.schema.json` (it is being edited on a parallel branch). Do not edit
  `entity.schema.json` yourself; the lead verifies cross-branch equality at integration.
- `reference.schema.json` target pattern accepts any scheme, including `file://` — the
  schema cannot express the rejection rule; your Python validation must.
- Range locators lack ordering constraints in JSON Schema (end before start passes) — your
  Python models must enforce ordering.
- Do not allocate IDs against workspace state (no filesystem access): allocation policy
  (next-free-number) needs stored state and belongs to WP-200/WP-300. This package owns
  grammar, parsing, and validation only.
- New test directories (`tests/domain/`) need an `__init__.py`: pytest's default import
  mode collides on duplicate basenames against the flat `tests/test_*.py` files otherwise.
