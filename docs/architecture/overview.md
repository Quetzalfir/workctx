# Architecture overview

Work Context OS separates durable business context from agent conversations and optional indexes.

```text
Agent client
    -> project instructions and portable skills
    -> Work Context MCP / CLI application services
    -> domain contracts
    -> canonical filesystem + rebuildable SQLite projections
```

## Boundaries

- Domain code defines IDs, references, artifacts, observations, claims, tasks, relations, and transactions.
- Application services orchestrate use cases.
- Adapters handle filesystems, SQLite, agent configuration, secrets, and optional plugins.
- CLI and MCP are presentation/tool boundaries over the same application services.

See `.agents/plan/initial/02-architecture.md` for the initial implementation detail and `docs/adr/` for accepted decisions.
