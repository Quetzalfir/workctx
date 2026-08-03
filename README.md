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

This repository contains the complete first-alpha implementation of the Phase 1 scope (CLI and durable core). The `0.1.0-alpha` release is prepared from this repository; see the [release notes](docs/releases/0.1.0-alpha.md) for the exact feature list, known limitations, and stability policy.

Implemented and tested today:

- isolated context workspaces created from a versioned template;
- the canonical Markdown/YAML store with locking, staging, and atomic writes;
- workspace validation with stable diagnostic codes;
- rebuildable SQLite/FTS projections with typed queries and full-text search;
- retrieval, deterministic context packs, and a read-only operational brief;
- the transaction engine with an append-only, hash-chained audit ledger;
- the inbox artifact lifecycle: registration, hashing, safety quarantine, and post-commit archive;
- generated operational views (current focus, next actions, waiting on, stale knowledge, brief);
- local outbox drafting with no send capability;
- a stdio MCP server bound to one context (`workctx mcp serve`);
- agent adapter installers for Codex, Claude Code, and Gemini CLI;
- deterministic migration of legacy Markdown repositories (`workctx migrate legacy`);
- an end-to-end acceptance suite that drives the public CLI and MCP surfaces.

Phase 1 is intentionally CLI-first. A graphical interface belongs to a later phase.

## Install

The alpha is installed from a source checkout:

```powershell
git clone https://github.com/Quetzalfir/workctx
cd workctx
uv tool install ".[mcp]"
workctx version
```

`pipx install ".[mcp]"` from the same checkout is an equivalent alternative. The `[mcp]` extra is only required for `workctx mcp serve`; every other command works without it. For a development environment, use `uv sync` as shown in the contributor quick start below.

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

## The six-step cycle

Every piece of evidence moves through the same auditable cycle. Each step has a real command surface today:

1. **Capture.** Put the raw file below `00_inbox/raw/` and register it. Registration hashes the bytes, writes a manifest, and quarantines suspicious content instead of processing it.

   ```powershell
   workctx inbox add 00_inbox/raw/standup-note.txt --source chat --event-date 2026-08-03
   ```

2. **Process.** An agent (or you) extracts observations and builds one typed transaction proposal covering every affected entity. Validate it without touching the workspace:

   ```powershell
   workctx proposal validate proposal.json
   workctx transaction apply proposal.json --dry-run
   ```

3. **Approve and apply.** Apply is explicit: without `--yes` the command only previews. An approved apply is atomic across all files and appends one hash-chained audit event.

   ```powershell
   workctx transaction apply proposal.json --yes
   workctx transaction history
   ```

4. **Rebuild projections.** SQLite indexes and generated views are disposable and rebuilt from canonical files.

   ```powershell
   workctx index rebuild
   workctx view rebuild
   ```

5. **Retrieve.** Ask the workspace, not the chat history.

   ```powershell
   workctx search "reporting pipeline"
   workctx brief
   workctx ref trace workctx://<context-id>/task/TASK-2026-001
   workctx context-pack workctx://<context-id>/task/TASK-2026-001
   ```

6. **Draft.** Agents save reply and status drafts to `05_outbox/` through the MCP `draft_save` tool. Drafts are local canonical documents; nothing in the alpha can send, post, or publish them.

The original artifact is archived under `01_processed/` only after its transaction commits.

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

## Documentation map

| Start here | |
| --- | --- |
| [Quick start](docs/guides/quickstart.md) | Install, create a context, and run the full cycle. |
| [Concepts](docs/concepts.md) | The vocabulary: evidence, observations, claims, transactions, views. |
| [Release notes 0.1.0-alpha](docs/releases/0.1.0-alpha.md) | What shipped, known limitations, stability policy. |
| [Security and privacy](docs/security-and-privacy.md) | Trust model, quarantine, approval gates, and what is not protected. |

| Guides | |
| --- | --- |
| [Context layout](docs/guides/context-layout.md) | What each workspace directory means. |
| [Evidence processing](docs/guides/evidence-processing.md) | The safe processing workflow in detail. |
| [Multiple contexts](docs/guides/multiple-contexts.md) | Isolation between companies and projects. |

| Reference | |
| --- | --- |
| [CLI envelope](docs/reference/cli-envelope.md) | Exit bands, JSON envelopes, context resolution. |
| [Reference system](docs/reference/reference-system.md) | Stable IDs, `workctx://` URIs, and source locators. |
| [Transactions](docs/reference/transactions.md) | Proposals, atomic apply, and the audit ledger. |
| [Inbox lifecycle](docs/reference/inbox.md) | Registration, quarantine, and archive semantics. |
| [Views](docs/reference/views.md) | Generated operational views. |
| [Drafting](docs/reference/drafting.md) | The local outbox and the no-send boundary. |
| [MCP server](docs/reference/mcp.md) | The version 1 tool surface and envelopes. |
| [Agent adapters](docs/reference/agent-adapters.md) | Codex, Claude Code, and Gemini CLI installers. |
| [Legacy migration](docs/reference/migration.md) | Converting an existing Markdown repository. |
| [Architecture overview](docs/architecture/overview.md) | Layering and engine boundaries. |
| [Architecture decisions](docs/adr/README.md) | Accepted ADRs. |

The project direction is tracked in [`ROADMAP.md`](ROADMAP.md) and release history in [`CHANGELOG.md`](CHANGELOG.md).

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
uv run mypy src
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
