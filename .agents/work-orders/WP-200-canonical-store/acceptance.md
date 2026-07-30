# Acceptance criteria: WP-200-canonical-store

## Functional

- [ ] Byte-deterministic serialization with golden-file pin; re-serialization of parsed
      output is identity.
- [ ] CanonicalStore typed read/write for context config, entity frontmatter, task, and
      artifact manifest documents, zone-aware, boundary-enforced.
- [ ] Full ADR 0006 lock protocol with nonce, heartbeat, stale takeover, fencing.
- [ ] Staged multi-file replacement with write-ahead intent record and recovery
      inspection API.
- [ ] User-level registry APIs (platformdirs), idempotent, API-only.
- [ ] services/contexts.py writes through the serializer; signatures unchanged.

## Negative and edge cases

- [ ] Injected failure mid-replace-sequence: intent record survives; recovery API reports
      exact partial state.
- [ ] Injected PermissionError: bounded retry, then recoverable error; no partial
      visibility outside the staged targets.
- [ ] Stale takeover then old holder resumes: fence check aborts the old holder.
- [ ] Unparseable lock.json older than threshold is recovered; younger is respected.
- [ ] `..` traversal and symlink escape rejected (junction test may skip with recorded
      reason on unprivileged CI).
- [ ] Hand-edited file detection flags byte drift from canonical form.

## Quality

- [ ] All existing tests pass unchanged; frozen paths untouched.
- [ ] No new runtime dependencies; fictional fixtures only.
- [ ] docs/reference/canonical-store.md documents APIs and the 98_state layout.

## Commands

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```
