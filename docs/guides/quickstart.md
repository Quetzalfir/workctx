# Quick start

This guide walks the full cycle exactly as shipped in the first alpha: create an isolated context, capture evidence, apply an audited transaction, rebuild derived state, and retrieve the result. Every command below is implemented today.

## Install

From a source checkout:

```powershell
git clone https://github.com/Quetzalfir/workctx
cd workctx
uv tool install ".[mcp]"
workctx version
workctx doctor
```

`workctx doctor` reports the local Python, Git, and agent-client environment. For a development environment use `uv sync --all-groups --extra mcp` inside the checkout and prefix commands with `uv run`.

## Create a context

Each company or project gets its own isolated workspace:

```powershell
workctx context init D:\WorkContexts\example --name "Example" --id example
workctx validate D:\WorkContexts\example
```

`context init` copies the versioned workspace template; `validate` confirms layout, frontmatter, and reference integrity. The commands that follow accept `--context D:\WorkContexts\example` explicitly, or discover the context automatically when you run them from inside it.

## Capture evidence

Copy a raw file below `00_inbox/raw/`, then register it:

```powershell
workctx inbox add 00_inbox/raw/standup-note.txt --source chat --event-date 2026-08-03
workctx inbox list
```

Registration streams the file through SHA-256, writes a manifest with source metadata, and records one audit event. Suspicious content — possible secrets or instruction-like text — is quarantined under `00_inbox/quarantine/` instead of being processed; quarantined bytes are never parsed, executed, or copied into reports.

## Process evidence into a transaction

Processing turns raw evidence into observations, claims, tasks, and relations, applied as one atomic transaction. This is normally agent work: the installed skills and MCP tools guide an agent to extract observations, resolve duplicates and contradictions, and build a single typed proposal (see the [evidence processing guide](evidence-processing.md)).

The same surface is available directly. Given a proposal file (the format is documented in the [transactions reference](../reference/transactions.md)):

```powershell
workctx proposal validate proposal.json
workctx transaction apply proposal.json --dry-run
workctx transaction apply proposal.json --yes
```

Apply previews by default; `--yes` is the explicit approval. An approved apply updates every affected canonical file atomically under the context lock and appends one event to the hash-chained audit ledger. The processing workflow archives the original artifact under `01_processed/` only after its transaction commits — never before. The audit trail is queryable:

```powershell
workctx transaction history
```

## Rebuild derived state

The SQLite/FTS index and the generated views are disposable projections. Rebuild them at any time — deleting them loses nothing:

```powershell
workctx index rebuild
workctx view rebuild
```

`view rebuild` regenerates the operational views under `04_views/`: current focus, next actions, waiting on, stale knowledge, and the daily brief.

## Retrieve

Ask the workspace instead of scrolling chat history:

```powershell
workctx search "reporting pipeline"
workctx brief
workctx task list
workctx ref show workctx://example/task/TASK-2026-001
workctx ref trace workctx://example/task/TASK-2026-001
workctx context-pack workctx://example/task/TASK-2026-001 --budget 12000
```

`search` queries canonical entities in the full-text projection. `ref trace` walks a task back through claims and observations to exact source locators. `context-pack` builds a bounded, traceable bundle for handing to a model.

## Open an AI agent

Install the agent adapters, then open a detected client. `agent install` prints a dry-run plan by default; add `--yes` to execute it:

```powershell
workctx agent install --agent all --context D:\WorkContexts\example --yes
workctx agent open D:\WorkContexts\example --agent codex
```

The installer generates each client's bridge file, portable skills, and a project-scoped MCP configuration pointing at `workctx mcp serve` for this context. Inside the session, ask naturally:

```text
There is new evidence in 00_inbox. Process it, compare it with prior evidence,
update the related tasks and knowledge, and draft a response for Alex.
Do not send or publish anything.
```

The agent runs the same cycle you just ran by hand: register, propose, apply with approval, rebuild, retrieve — and saves any requested draft to `05_outbox/` through the approval-gated `draft_save` MCP tool. Drafts are local files; the alpha has no send capability of any kind.

## Migrate an existing Markdown repository

If you already keep work notes in a Markdown repository, convert them into a context:

```powershell
workctx migrate legacy D:\old-notes D:\WorkContexts\migrated
workctx migrate legacy D:\old-notes D:\WorkContexts\migrated --apply
```

Preview is the default and writes nothing. See the [migration reference](../reference/migration.md) for findings, overrides, and limitations.

## Where to go next

- [Context layout](context-layout.md) — what each directory means.
- [Evidence processing](evidence-processing.md) — the safe processing workflow.
- [Security and privacy](../security-and-privacy.md) — trust model and approval gates.
- [Release notes](../releases/0.1.0-alpha.md) — known limitations of this alpha.
