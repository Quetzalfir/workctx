# Acceptance criteria: WP-320-agent-installers

## Functional

- [ ] Detection per client via fake executable discovery.
- [ ] Install generates adapters + schema-valid manifest with canonical hashes.
- [ ] Status detects staleness per file; repair regenerates only stale files;
      reinstall is a no-op when clean.
- [ ] Uninstall removes only manifest-listed files; backups for modified user files.
- [ ] open_context spawns the selected client (mocked) or fails clearly.
- [ ] MCP-config seam reserved and reported NOT-IMPLEMENTED (D-014).

## Negative and edge cases

- [ ] Single-client install touches no other client's directories.
- [ ] User-created file alongside generated ones survives uninstall.
- [ ] Hand-edited generated adapter flagged as drift.
- [ ] No credential reading/writing anywhere (negative test on API surface and
      generated output).
- [ ] Unsafe/absolute generated paths refused (manifest schema honored).

## Quality

- [ ] Tests use isolated fake home/project dirs (doc-07); no real clients needed.
- [ ] docs/reference/agent-adapters.md documents per-client strategy and the MCP seam.
- [ ] Frozen paths untouched; no new runtime dependencies; fictional fixtures only.

## Commands

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```
