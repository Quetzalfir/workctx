# Acceptance criteria: WP-330-mcp-server

## Functional

- [ ] Discovery lists exactly the ADR 0012 surface, schema_version 1 everywhere.
- [ ] Implemented read tools return engine-faithful data on fixtures; Wave 4
      dependents return structured NOT-IMPLEMENTED.
- [ ] Mutation tools: structural failure without approved: true; transaction_apply
      end-to-end against a fixture proposal.
- [ ] Read-only resources for canonical entities and context config.
- [ ] workctx mcp serve wired; clean stdio lifecycle in an SDK-client test.
- [ ] mcp extra enabled for dev/CI; absent extra yields a clear
      unavailable-dependency error.

## Negative and edge cases

- [ ] Cross-context URIs and path escapes refused on every tool.
- [ ] No tracebacks or secret-looking values in any tool result.
- [ ] Invalid tool input fails schema validation structurally.
- [ ] SDK-dependent tests skip cleanly (recorded reason) without the extra.

## Quality

- [ ] pyproject/ci edits limited to the mcp extra enablement.
- [ ] docs/reference/mcp.md documents surface, versioning policy, and client config.
- [ ] Frozen paths untouched; fictional fixtures only.

## Commands

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```
