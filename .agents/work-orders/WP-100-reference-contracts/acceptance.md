# Acceptance criteria: WP-100-reference-contracts

## Functional

- [ ] Every doc-03 ID family (ART, EVD, EVD#OBS, TASK, TASK-STNN, DEC, RISK, Q, CLM, PER,
      SYS) parses, validates, and formats with tested valid and invalid cases.
- [ ] Canonical entity-type enum in `domain/vocabulary.py` equals the D-018 19-value list
      exactly (tested against the literal list) and is the single Python vocabulary source.
- [ ] Observation and Claim models in `domain/observations.py` / `domain/claims.py`
      round-trip fixtures against their schemas.
- [ ] `workctx.models.reference` imports still work unchanged (shim); `WorkctxUri.parse`,
      `__str__`, and `require_context` behavior is unchanged (existing tests pass unmodified).
- [ ] `artifact://sha256/<64-hex>` and `repo://<id>@<commit>/<path>#L<start>-L<end>` parse
      and format; repo references without a commit are rejected.
- [ ] All 9 locator types validate with range-ordering enforcement.
- [ ] Typed-relation enum matches schemas/reference.schema.json exactly.
- [ ] Normalization helper converts literal `#OBS-` authoring form to `%23` canonical form.

## Negative and edge cases

- [ ] Literal `#` in a workctx:// URI is rejected with an actionable message.
- [ ] Absolute machine paths (POSIX and `C:\` forms) and `file://` are rejected as durable
      references.
- [ ] `%2E%2E` traversal and empty segments remain rejected.
- [ ] Locators with `end < start` (lines, pages, ms) are rejected.
- [ ] Unknown relation types and entity types are rejected.

## Quality

- [ ] Contract-test fixtures validate against the four owned JSON Schemas AND round-trip
      through the Python models (ADR 0008), with at least one negative fixture per model
      demonstrating divergence would be caught.
- [ ] Domain modules import no CLI/adapter libraries.
- [ ] Tests added; docs/reference/reference-system.md updated where behavior clarifies it.
- [ ] Allowed paths only; `models/__init__.py` and `domain/__init__.py` untouched.
- [ ] No secrets or private data.

## Commands

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```
