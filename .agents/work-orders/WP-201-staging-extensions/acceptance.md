# Acceptance criteria: WP-201-staging-extensions

## Functional

- [ ] Intent targets carry kinds: replace (unchanged), move, delete — applied
      atomically in sequence order.
- [ ] Recovery inspection/completion/rollback cover all kinds and mixed sequences.
- [ ] Fenced append: nonce-verified, fsynced, bounded-retry, parent-dir creation
      inside the boundary, usable while an intent is active, finalize unchanged.
- [ ] docs/reference/canonical-store.md updated.

## Negative and edge cases

- [ ] Injected mid-sequence failure per kind and mixed → correct partial-state report,
      completion and rollback both work.
- [ ] Fence mismatch aborts append without writing.
- [ ] Move to existing destination refused; move/append escape attempts refused.
- [ ] Torn-line injection test shows complete lines only.
- [ ] Old intent.json records: documented compatibility behavior verified by test.

## Quality

- [ ] All pre-existing tests/filesystem tests pass unmodified.
- [ ] Frozen paths untouched; no new runtime deps; fictional fixtures only.

## Commands

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```
