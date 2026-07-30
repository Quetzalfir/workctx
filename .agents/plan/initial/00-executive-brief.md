# Executive brief

## Product intent

Work Context OS is a local-first operational memory for people who use AI agents across multiple jobs, companies, products, or projects.

The system captures unstructured evidence, transforms it into durable structured knowledge and work state, and makes that context available to a new AI session without requiring the user to remember or restate every detail.

## Problem

Ordinary AI chats lose continuity, mix facts with inference, and make it difficult to prove where a conclusion came from. Ad hoc Markdown repositories improve continuity but become fragile when an agent must manually update many indexes, task files, people files, focus documents, and evidence registries for one new input.

The new system must preserve the strengths of the earlier repository method while replacing fragile conventions with deterministic tooling.

## Phase 1 outcome

A user can:

1. install `workctx` as a CLI tool;
2. create multiple isolated contexts;
3. open any context in Codex, Claude Code, or Gemini CLI;
4. add evidence to an inbox;
5. ask an agent to process it using portable skills and `workctx` tools;
6. preserve precise references, tasks, people, decisions, risks, and relationships;
7. start a new agent session and recover the same operational context;
8. validate and rebuild all derived state;
9. inspect every mutation through an audit trail;
10. operate without a graphical interface or paid graph product.

## Non-goals for Phase 1

- no custom desktop or web UI;
- no hosted SaaS;
- no team tenancy or centralized identity;
- no autonomous external writes by default;
- no requirement for Graphify, CodeGraph, Obsidian, a vector database, or a graph database;
- no bundled employer-specific connector credentials;
- no attempt to replace Jira, Confluence, GitHub, or observability platforms.

## Technical direction

- Python 3.12+
- `uv` for project and tool workflows
- Typer and Rich for CLI experience
- Pydantic models and JSON Schema at boundaries
- canonical Markdown/YAML workspace
- SQLite and FTS as rebuildable local projections
- official MCP Python SDK for the model-neutral tool boundary
- Git for versioning and parallel agent worktrees

## Delivery strategy

Implementation is split into dependency-aware work packages. Independent work may run in parallel only when writable paths do not overlap and contracts are explicit. Every delivery is reviewed by the implementation lead using actual diffs and executed tests.

## Success signal

The decisive acceptance scenario is not merely that files exist. It is that a fresh agent session can answer:

> What is the current state of TASK-2026-001, what evidence supports it, what changed most recently, who are we waiting on, and what should I say to that person?

The answer must be traceable, current, context-isolated, and reproducible from canonical workspace data.
