# Quick start

This guide describes the intended Phase 1 experience. Some commands remain implementation targets in the scaffold.

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

After agent installers are implemented:

```powershell
workctx agent install --agent all --context D:\WorkContexts\example
workctx agent open D:\WorkContexts\example --agent codex
```

Then ask naturally:

```text
There is new evidence in 00_inbox. Process it, compare it with prior evidence,
update the related tasks and knowledge, and draft a response for Alex.
Do not send or publish anything.
```
