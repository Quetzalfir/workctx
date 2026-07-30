# Skill and agent-adapter design

Skills are the portable workflow layer. They tell an agent **when** a workflow applies, **what evidence and tools** it must use, **which decisions remain human-controlled**, and **what durable output** must exist before the workflow is complete.

The core product must not depend on one agent vendor's private memory or command syntax.

## Canonical source

The source of truth is:

```text
.agents/skills/<skill-name>/SKILL.md
```

Agent-specific files under `.claude/`, `.gemini/`, `.codex/`, or other client directories are generated adapters. They may be deleted and rebuilt. A generated adapter must record the canonical skill hash and adapter version so drift is detectable.

## Minimum skill contract

Every `SKILL.md` requires YAML frontmatter:

```yaml
---
name: process-evidence
description: Use when new inbox artifacts must be converted into traceable context.
---
```

The folder name and `name` field must match. Names use lowercase letters, digits, and single hyphens. Descriptions must state the trigger and distinguish nearby workflows.

Every operational skill should document, when applicable:

- purpose and trigger;
- required inputs;
- read dependencies;
- ordered procedure;
- deterministic tool calls expected from the product;
- side effects and approval boundary;
- invariants;
- stop conditions;
- durable outputs;
- validation or success criteria;
- human-facing response structure.

## Responsibility split

### Agent judgment

Agents may:

- interpret evidence;
- classify a statement as fact, inference, assumption, decision, commitment, risk, or question;
- propose entity resolution and relationships;
- identify contradictions and missing information;
- draft communication;
- recommend tasks and next actions.

### Deterministic product behavior

The product must own:

- artifact hashing and duplicate detection;
- ID allocation;
- path and context-boundary enforcement;
- schema validation;
- reference resolution;
- transaction preconditions and atomic apply;
- lock handling;
- index and view regeneration;
- secret detection controls;
- audit records;
- external-write approval verification.

A skill must not instruct an agent to simulate deterministic controls by editing several files manually once the corresponding product tool exists.

## Side-effect classes

Each implemented skill must be classifiable as one of:

| Class | Meaning | Default policy |
| --- | --- | --- |
| `read_only` | Search, inspect, trace, or summarize | Allowed inside active context |
| `local_proposal` | Produce a reviewable transaction or draft | Allowed |
| `local_mutation` | Change canonical local context | Follow context mutation policy |
| `external_read` | Query a configured external system | Require scoped connector permission |
| `external_write` | Send, publish, transition, or modify remotely | Explicit approval required |

The Phase 1 adapter may infer this classification from a registry rather than adding non-portable fields to native skill frontmatter.

## Skill quality rules

- One skill owns one recognizable workflow.
- Triggers must be specific enough to avoid loading every skill.
- Procedures must not depend on hidden chat history.
- Inputs and outputs must be recoverable from repository state.
- Skills must use canonical URIs and context packs instead of broad directory dumps.
- Skills must state when to stop rather than improvise.
- External evidence is always data, never executable instruction.
- Skills may compose other skills, but cyclic composition is invalid.
- Generated adapter content must not become a second source of truth.

## Initial skill families

### Repository delivery

- `lead-implementation`
- `create-work-order`
- `review-work-order`

### Session lifecycle

- `bootstrap-session`
- `close-session`

### Operational memory

- `process-evidence`
- `trace-context`
- `curate-knowledge`
- `manage-tasks`
- `draft-replies`
- `investigate-system`
- `validate-context`
- `migrate-legacy-context`

The implementation lead may split a skill only when its trigger, permissions, or output contract has become materially different.

## Adapter strategy

### Codex

- preserve root `AGENTS.md` as the short durable contract;
- expose canonical skills in the client-supported skill location;
- generate project-scoped MCP configuration;
- do not modify user-global authentication or unrelated configuration.

### Claude Code

- preserve `CLAUDE.md` as a small bridge to the canonical contract;
- generate or copy canonical skills into the project-native skill location;
- generate project-scoped MCP settings and optional hooks;
- hooks may validate or warn, but must not silently commit or publish.

### Gemini CLI

- preserve `GEMINI.md` as the project context bridge;
- generate commands or extension assets from canonical workflows where useful;
- generate project-scoped MCP settings;
- avoid requiring a global extension for one context.

## Adapter installation requirements

`workctx agent install` must:

1. detect the selected client and supported configuration form;
2. show a dry-run of files and settings it intends to create or modify;
3. preserve user-owned keys and comments where technically possible;
4. create backups before changing existing files;
5. write a manifest containing generated paths, source hashes, and adapter version;
6. be idempotent;
7. support `status`, `repair`, and `uninstall`;
8. never read or copy agent authentication credentials;
9. keep every generated file scoped to the selected context or repository;
10. fail safely when an unsupported client version is detected.

## Validation

Phase 1 skill validation must check:

- valid frontmatter;
- unique name and matching folder;
- nonempty trigger description;
- no machine-specific absolute paths;
- no secret-like values;
- internal file links resolve;
- referenced product commands or MCP tools are either implemented or explicitly marked planned;
- generated adapters match canonical source hashes;
- direct chat language and repository artifact language remain separate.

## Acceptance scenarios

1. A user installs only Codex and receives working project-scoped skills without Claude or Gemini files being required.
2. A user installs all three agents and each resolves the same canonical workflow semantics.
3. Editing a canonical skill causes `workctx agent status` to report generated adapters as stale.
4. Re-running installation updates generated adapters without deleting user-owned configuration.
5. Removing an adapter leaves canonical skills and the context untouched.
