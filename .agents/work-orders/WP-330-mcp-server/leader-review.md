# Leader review: `WP-330-mcp-server`

## Decision

`accepted`

## Contract compliance

- Base `925aa08` (via pin `39d47f1`); delivery `1bbb7f5`, report `8bd7681`; clean.
- Path audit: all files inside `allowed_paths`. The narrow grants were honored beyond
  the letter: `pyproject.toml` untouched (extra enabled via one CI line,
  `uv sync --locked --all-groups --extra mcp`); `cli.py` gained exactly one `mcp`
  sub-app with a lazily-imported `serve` command.

## Diff review

- `src/workctx/mcp/`: the exact ADR 0012 17-tool surface (11 read + 6 approval-gated
  mutation) with schema_version-1 contracts; structural `$.approved` failures verified
  parametrically; NOT-IMPLEMENTED structured placeholders for WP-310/WP-400/WP-420
  dependents as contracted.
- Denial coverage: per-tool cross-context and path-escape refusal
  (`test_every_tool_refuses_cross_context_and_path_escape_attempts`), foreign-context
  resources refused, symlink escape refused without leaking the target, projected path
  escape refused before any file read.
- Sanitization: recursive redaction of success results; structured errors reuse CLI
  diagnostic codes; no tracebacks cross the boundary.
- Lazy SDK loading with a clear unavailable-dependency error; SDK-dependent tests skip
  cleanly without the extra; stdio lifecycle covered by an SDK-client integration test.

## Validation executed

| Command | Result | Notes |
| --- | --- | --- |
| report.json vs agent-report.schema.json | pass | validated by lead |
| `uv run ruff check .` / mypy | pass | 72 source files |
| `uv run pytest` (with mcp extra) | pass | 1046 passed; 108 MCP tests |

## Findings

- Unresolved items are the contracted planned wirings (WP-310 ingestion tools, WP-420
  draft_save, WP-400 enrichment) behind stable tool names — correct per ADR 0012.

## Integration notes

- Integrated before WP-320-r3 (disjoint). Its server identity unblocks the lead's
  WP-320 MCP-config finalization at wave close.
