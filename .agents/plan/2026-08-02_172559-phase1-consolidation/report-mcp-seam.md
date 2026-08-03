# Report: LEAD-W2 — MCP-config seam finalization

Worker: Codex gpt-5.6-sol (max effort), session `019fc4d7-21fb-7a51-8962-dbd5f4ebe848`,
one lead correction round. Report authored by the lead from the worker's final
messages because the sandbox mounts `.agents/` read-only.

## Delivered

- Manifest MCP component widened from `Literal["not_implemented"]` to the real state
  set with generated path + content hash (manifest.py), schema updated with positive
  and negative fixtures (incl. `mcp-adapter-path-mismatch`, `mcp-generated-missing-hash`).
- Per-client project-scoped MCP config generation (renderers.py):
  claude `.mcp.json`, codex `.codex/config.toml`, gemini `.gemini/settings.json`,
  each registering `workctx mcp serve --context .`; generate-if-absent, user-owned
  divergence reported, D-032 three-factor authority applies (correction round extended
  the credential-safe ownership set by exactly `mcp_configuration_path(client)`).
- `_feature_mcp()` is real detection (path+hash vs manifest); manifest write derives
  from the install plan.
- Kit prose lint gained an implemented-MCP-tool allowlist sourced from
  `workctx.mcp.contracts.TOOL_CONTRACTS` (`mcp__workctx__<tool>` qualified names),
  mirroring `_is_implemented_command`; unknown tools still fail.
- Docs: agent-adapters.md + skill-adapters.md seam sections updated.

## Validation (lead-run, outside the sandbox)

| Command | Result |
| --- | --- |
| `uv run pytest tests/agents_setup -q` | 286 passed, 6 skipped |
| `uv run pytest -q` (full) | 1344 passed, 6 skipped |
| `uv run mypy src` | clean, 87 files |
| `uv run ruff check .` / format | clean |

## Correction round

The worker's sandboxed pytest was masked by temp-dir ACL failures; the lead's real
gate exposed 6 ownership-set failures in test_transactions.py, returned via
`codex exec resume` with the root cause, fixed with a single exact-path allowlist
addition. No other changes.
