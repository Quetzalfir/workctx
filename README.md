# Work Context OS (`workctx`)

> A local-first, model-neutral work memory and operations system for AI coding agents.

`workctx` turns scattered evidence—meeting transcripts, chats, screenshots, documents, repository findings, issue trackers, and notes—into a durable, traceable, and queryable work context.

It is designed so a user can open a company or project workspace with Codex, Claude Code, Gemini CLI, or another compatible agent and ask ordinary questions such as:

```text
There is new evidence in 00_inbox. Process it, compare it with prior evidence,
update the related work, and draft what I should tell Alex. Do not send anything.
```

The conversation is temporary. The workspace is the memory.

## Current status

This repository is a **working pre-alpha implementation and engineering plan**, not a finished release. It contains:

- the product and architecture specification;
- a concise repository-wide agent contract;
- a multi-agent leadership and delegation protocol;
- improved evidence and reference models;
- canonical skills for repeated workflows;
- a reusable workspace template;
- JSON Schemas for core contracts;
- the Phase 1 engines: canonical store, validation, SQLite/FTS projections, retrieval and context packs, the transaction engine with audit ledger, and the inbox artifact lifecycle;
- an executable CLI covering context, validation, index, search, ref, context-pack, proposal, transaction, task, agent, and MCP commands;
- a stdio MCP server bound to one context (`workctx mcp serve`);
- agent installers for Codex, Claude Code, and Gemini CLI;
- a staged implementation backlog and test strategy.

Evidence extraction rails, generated operational views, and outbox drafting are the remaining Phase 1 packages and are in flight.

Phase 1 is intentionally CLI-first. A graphical interface belongs to a later phase.

## Local operator preferences

The public repository stays language-neutral. Copy `.agents/operator.example.yaml` to `.agents/operator.local.yaml` to select a local display name and interaction language for implementation agents. The local file is ignored by Git and must not be published. Repository artifacts remain English regardless of the selected conversation language.


## Core principles

1. **Model-neutral:** the durable context does not belong to one model or chat thread.
2. **Local-first:** canonical data lives in user-controlled files.
3. **Markdown/YAML source of truth:** SQLite and other indexes are rebuildable projections.
4. **Context isolation:** each company or project is a separate security boundary.
5. **Evidence before claims:** important statements retain precise source locators.
6. **Transactional updates:** one operation updates all affected entities or none.
7. **Auditable automation:** every mutation records who, what, when, why, and source.
8. **Human control:** external writes require explicit approval by default.
9. **No secrets in workspaces:** store only secret references, never secret values.
10. **Portable skills:** recurring workflows are packaged independently from any one agent.

## Target experience

After installation, a user can create and open multiple isolated contexts:

```powershell
workctx context init D:\WorkContexts\new-company --name "New Company" --profile architect
workctx context init D:\WorkContexts\product-platform --name "Product Platform" --profile hybrid

workctx agent open D:\WorkContexts\new-company --agent codex
workctx agent open D:\WorkContexts\product-platform --agent claude
```

These commands are implemented: `workctx agent install` detects supported clients, installs the portable skills, and generates each client's project-scoped MCP configuration, and `workctx agent open` launches the selected client in the context root. Inside either session, the user can speak naturally. The agent uses the same persistent workspace and the same `workctx` MCP server.

## Architecture

```text
Codex / Claude Code / Gemini CLI / other agents
                         |
              AGENTS.md + portable skills
                         |
                 Work Context MCP
                         |
       +-----------------+------------------+
       |                 |                  |
Canonical Markdown   SQLite/FTS       Optional plugins
and YAML             projections      Graphify, CodeGraph,
                                      Jira, GitHub, Dynatrace
```

Canonical content remains usable even when all generated state is deleted.

## Workspace lifecycle

```text
00_inbox
    -> register artifact and source locator
    -> extract observations, decisions, risks, tasks, and relationships
    -> resolve duplicates and contradictions
    -> propose one validated transaction
    -> update canonical knowledge and work
    -> regenerate views and indexes
    -> move the original to 01_processed
    -> create drafts in 05_outbox when requested
```

Registration, hashing, quarantine, duplicate detection, transactional apply, index rebuild, and post-commit archive to `01_processed/` are implemented today. Evidence extraction rails, generated views, and `05_outbox` drafting are the remaining Phase 1 packages.

## Repository layout

```text
.agents/                 Agent skills, plans, contracts, and work-order templates
docs/                    Product, architecture, reference, and contributor docs
schemas/                 JSON Schemas for workspace and agent contracts
src/workctx/             Python package: domain, engines, adapters, CLI, and MCP server
templates/context/       Reusable isolated workspace template
tests/                   Unit, contract, integration, and acceptance tests
```

Read [`START-HERE.md`](START-HERE.md) before asking an implementation agent to work on this repository. The manual multi-agent procedure is documented in [`docs/development/implementation-lead-guide.md`](docs/development/implementation-lead-guide.md).
The checks executed against this generated scaffold are recorded in [`docs/development/scaffold-validation.md`](docs/development/scaffold-validation.md).

## Contributor quick start

Requirements:

- Python 3.12 or newer
- Git
- `uv`

```powershell
uv sync --all-groups --extra mcp
uv run workctx version
uv run pytest
uv run ruff check .
```

Create a sample context with the current scaffold:

```powershell
uv run workctx context init .sandbox/demo-context --name "Demo Context" --id demo-context
uv run workctx validate .sandbox/demo-context
```

## Agent support strategy

The canonical rules and skills live in this repository. Agent-specific adapters are generated by `workctx agent install` rather than maintained as divergent copies:

- Codex: `AGENTS.md` bridge, native `.agents/skills/`, MCP entry in `.codex/config.toml`
- Claude Code: `CLAUDE.md` bridge, generated `.claude/skills/`, MCP entry in `.mcp.json`
- Gemini CLI: `GEMINI.md` bridge, generated `.gemini/skills/`, MCP entry in `.gemini/settings.json`

An absent MCP configuration file is generated; an existing one stays user-owned and is never rewritten. See [`docs/reference/agent-adapters.md`](docs/reference/agent-adapters.md) for detection markers, drift, and repair semantics.

MCP is the stable tool boundary. Instruction files only bootstrap the agent and tell it when to use those tools.

## Optional future integrations

These are not required for the core:

- Graphify for broad architecture relationships across code and documents;
- CodeGraph for code-level symbols, callers, callees, and impact analysis;
- Obsidian as a human-facing view over the same Markdown files;
- Jira, Confluence, GitHub, Dynatrace, Rally, Teams, and other connectors;
- Graphiti or another temporal graph only when temporal retrieval requirements justify it.

## License

Apache License 2.0. See [`LICENSE`](LICENSE).
