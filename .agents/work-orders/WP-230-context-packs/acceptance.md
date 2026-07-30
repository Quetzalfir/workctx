# Acceptance criteria: WP-230-context-packs

## Functional

- [ ] Resolution of workctx:// (via projection), artifact://, and repo:// references.
- [ ] Inbound/outbound typed traversal with depth control.
- [ ] Trace: claim → observation → source locator, exact (UC-004).
- [ ] Deterministic documented ranking; per-factor ordering unit tests.
- [ ] Ten-section budgeted packs with truncation metadata; identical inputs → identical
      packs.
- [ ] Current-vs-superseded handling with history on request.
- [ ] schemas/context-pack.schema.json + positive/negative fixtures; packs validate.

## Negative and edge cases

- [ ] Cross-context reference refused at the retrieval boundary.
- [ ] Unknown entity → clear not-found result, not an exception leak.
- [ ] Entity with no relations → valid minimal pack.
- [ ] Budget smaller than the focal entity → minimal pack with explicit truncation
      metadata, never an invalid pack.
- [ ] Secret-looking workspace values do not appear in pack summaries.

## Quality

- [ ] Consumes WP-210 query APIs only; no SQL, no adapter edits; frozen paths untouched.
- [ ] docs/reference/context-packs.md documents ranking factors, budget units, and
      truncation order.
- [ ] Fictional fixtures only.

## Commands

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```
