# Architecture specification

## Architectural style

Use a ports-and-adapters structure with a small domain core. The CLI, MCP server, filesystem, SQLite, Git, agent installers, and future plugins are adapters around application services.

Domain logic must not import Typer, Rich, MCP, SQLite, or agent-specific configuration modules.

## Logical components

```text
Presentation
├── CLI
└── MCP server

Application
├── context lifecycle
├── artifact ingestion
├── transaction proposal and apply
├── reference resolution
├── retrieval/context packs
├── task and knowledge services
├── validation and repair
└── view/index rebuild

Domain
├── identities and URIs
├── artifacts and source locators
├── observations and claims
├── entities and typed relations
├── work items and state transitions
├── transaction operations
└── policy decisions

Adapters
├── canonical filesystem store
├── SQLite/FTS projection
├── Git metadata
├── operating-system keyring references
├── agent configuration installers
└── optional connector and graph plugins
```

## Canonical versus derived data

### Canonical

- context configuration;
- artifact manifests;
- preserved evidence;
- evidence notes and atomic observations;
- knowledge entities;
- tasks and state history;
- decisions, risks, questions, and claims;
- approved drafts and external-action receipts;
- audit ledger or its cryptographically linked canonical representation.

### Derived and rebuildable

- SQLite tables and FTS indexes;
- backlinks and relationship indexes;
- current-focus and next-action views;
- summaries and dashboards;
- Graphify and CodeGraph indexes;
- caches and embeddings.

Generated files must declare their generator and source revision and must never be edited as canonical state.

## Context boundary

A context root contains `context.yaml` and a stable context ID. All paths, indexes, tools, plugin instances, credentials references, and searches are scoped to that root.

The application layer receives a resolved `ContextHandle` for every operation. Global implicit context is prohibited in domain code.

## Transaction model

An evidence or work mutation is represented as a transaction proposal:

```text
proposal ID
base context revision
actor and agent metadata
source references
ordered operations
preconditions
postconditions
expected generated views
```

Apply algorithm:

1. acquire the context write lock;
2. confirm base revision and operation preconditions;
3. validate all proposed documents and references in memory/staging;
4. write staged canonical files;
5. atomically replace canonical targets;
6. append the audit event;
7. update context revision;
8. rebuild affected projections;
9. release the lock;
10. return a complete result or a recoverable projection warning.

Canonical mutation must not be partially visible. Projection rebuild failure must not erase a committed canonical transaction; it must mark derived state stale and return repair instructions.

## Storage strategy

### Canonical filesystem

Use UTF-8 Markdown with YAML frontmatter for narrative entities and YAML or JSON for machine-oriented manifests and ledgers. Preserve stable ordering where practical to reduce noisy diffs.

### SQLite projection

Use SQLite for:

- entity metadata;
- typed reference edges;
- observation and claim lookup;
- task state queries;
- aliases;
- FTS search;
- generated-view inputs;
- transaction and projection status.

SQLite is not the sole copy of user knowledge.

## Concurrency

- multiple readers are allowed;
- one canonical writer per context;
- use an OS-safe lock file with owner/session metadata and stale-lock recovery;
- transaction preconditions detect write skew;
- parallel development agents use separate Git worktrees and non-overlapping path ownership;
- generated projections use temporary files and atomic replacement.

## Package direction

Proposed Python package layout:

```text
src/workctx/
├── domain/
│   ├── ids.py
│   ├── references.py
│   ├── artifacts.py
│   ├── observations.py
│   ├── entities.py
│   ├── tasks.py
│   ├── transactions.py
│   └── policies.py
├── application/
│   ├── contexts.py
│   ├── ingestion.py
│   ├── retrieval.py
│   ├── mutations.py
│   ├── validation.py
│   └── projections.py
├── adapters/
│   ├── filesystem/
│   ├── sqlite/
│   ├── agents/
│   ├── secrets/
│   └── plugins/
├── cli/
├── mcp/
└── bootstrap/
```

The current scaffold is smaller. The implementation lead may migrate it incrementally rather than perform a large unverified rewrite.

## MCP boundary

Expose stable, task-oriented tools rather than raw file-edit tools. Initial categories:

- context bootstrap and health;
- search and entity retrieval;
- reference resolution and context packs;
- artifact registration and inbox inspection;
- transaction proposal, validation, and apply;
- task operations;
- draft generation inputs and outbox persistence;
- session close and audit summary.

MCP resources should expose read-only context views and canonical entities. MCP prompts may expose portable workflows, but skills remain the richer repository-distributed workflow format.

## Plugin boundary

Plugins must declare:

- plugin ID and version;
- capabilities;
- required permissions;
- context-scoped configuration schema;
- secret references;
- read versus write operations;
- health checks;
- data normalization contract;
- audit and idempotency behavior.

The core must function when no plugins are installed.

## Architecture decisions to confirm early

1. canonical frontmatter serialization library and ordering policy;
2. audit ledger representation and tamper-evidence level;
3. filesystem atomicity strategy on Windows;
4. lock implementation;
5. schema migration framework;
6. exact MCP tool surface for the first alpha;
7. whether JSON Schema files are generated from Pydantic or jointly maintained with contract tests.
