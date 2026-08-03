# Quick start

This guide describes the Phase 1 experience. The commands below are implemented; evidence extraction and outbox drafting still land in later Phase 1 packages.

## Install for development

```powershell
uv sync --all-groups
uv run workctx doctor
```

## Create a context

```powershell
uv run workctx context init D:\WorkContexts\example --name "Example" --id example
uv run workctx validate D:\WorkContexts\example
```

## Open an AI agent

Install the agent adapters, then open a detected client. `agent install` prints a dry-run plan by default; add `--yes` to execute it:

```powershell
workctx agent install --agent all --context D:\WorkContexts\example --yes
workctx agent open D:\WorkContexts\example --agent codex
```

Then ask naturally:

```text
There is new evidence in 00_inbox. Process it, compare it with prior evidence,
update the related tasks and knowledge, and draft a response for Alex.
Do not send or publish anything.
```
