# Acceptance criteria: WP-220-validation-engine

## Functional

- [ ] Typed-model validation of every canonical document with per-file coded diagnostics.
- [ ] Reference integrity: parse, context boundary, D-018 vocabulary, resolution to
      existing entities; external/artifact refs are advisories.
- [ ] Task hierarchy violations and blocks/depends_on contradiction cycles detected.
- [ ] Claim temporal rules: overlapping current single-valued claims and supersession
      cycles detected.
- [ ] FreshnessProbe protocol defined with a null implementation.
- [ ] Strict mode escalates warnings at API level.
- [ ] Structural checks (directories, UTF-8, secrets, paths, federated_search) preserved
      with stable CTX-* codes.

## Negative and edge cases

- [ ] One negative fixture per rule listed in the contract's first acceptance criterion,
      each producing exactly its expected code.
- [ ] Filename/frontmatter id mismatch diagnosed.
- [ ] Secret diagnostics report location only, never the matched value.
- [ ] Fresh `workctx context init` workspace validates clean.

## Quality

- [ ] Consumed interface unchanged: existing CLI validate tests pass unmodified.
- [ ] Code/doc sync test: every emitted code is documented in
      docs/reference/validation-diagnostics.md with a repair action.
- [ ] Frozen paths untouched; no new runtime deps; fictional fixtures only.

## Commands

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```
