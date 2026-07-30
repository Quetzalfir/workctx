# Implementation work packages

The implementation lead may refine package boundaries, but must preserve dependencies and acceptance intent.

## Execution waves

```text
Wave 0: WP-000, WP-001
Wave 1: WP-100, WP-110, WP-120, WP-130
Wave 2: WP-200, WP-210, WP-220, WP-230
Wave 3: WP-300, WP-310, WP-320, WP-330
Wave 4: WP-400, WP-410, WP-420
Release: WP-500
```

Work inside a wave is parallel only when path ownership is conflict-free and listed dependencies are complete.

## Wave 0 — Foundation

### WP-000 — Lead baseline and decision register

**Goal:** inspect the scaffold, confirm architecture assumptions, create status tracking, and record foundational decisions.

**Deliverables:**

- implementation status document;
- path ownership matrix;
- first ADRs for serialization, locking, migrations, and schema ownership;
- validated work-order templates;
- updated dependency graph if needed.

**Sequential:** yes. No implementation delegation before this is complete.

### WP-001 — Development foundation

**Goal:** make the repository reproducibly installable and green on supported platforms.

**Scope:** package layout, tooling, CI, lint, typing, tests, release metadata, contributor commands.

**Acceptance:** clean checkout can run the full baseline checks on Windows, macOS, and Linux CI.

## Wave 1 — Contracts and surfaces

### WP-100 — Domain identity, URI, and reference contracts

Implement stable IDs, canonical URIs, source locators, typed relations, and validation. This package owns the semantics described in `03-reference-and-retrieval-model.md`.

**Blocks:** WP-200, WP-210, WP-230, WP-300.

### WP-110 — Workspace schema and template

Implement context config, canonical directory rules, entity frontmatter contracts, artifact manifests, task hierarchy, claim and transaction schemas, and template generation inputs.

**Blocks:** WP-200, WP-220, WP-400.

### WP-120 — CLI framework and result envelope

Implement command grouping, context resolution shell, human/JSON result envelope, exit codes, and testable presentation boundaries without implementing all business commands.

**Blocks:** command integration in later packages.

### WP-130 — Portable skill contract and agent bridges

Define canonical skill structure, linting, trigger metadata, output contracts, and generation targets for Codex, Claude, and Gemini. Improve the initial skills without coupling them to unimplemented tool names.

**Blocks:** WP-320.

## Wave 2 — Durable core

### WP-200 — Canonical filesystem repository

Implement safe context discovery, canonical read/write adapters, normalized serialization, staging, atomic replacement, and path-boundary enforcement.

**Depends on:** WP-100, WP-110.

### WP-210 — SQLite and FTS projections

Implement schema, migrations, entity/reference indexing, full-text search, backlinks, projection metadata, and complete rebuild from canonical files.

**Depends on:** WP-100, WP-110.

### WP-220 — Validation and repair engine

Implement schema validation, ID/reference integrity, task hierarchy checks, claim temporal checks, secret/path checks, projection freshness, and actionable diagnostics.

**Depends on:** WP-110; integrates with WP-100 and WP-200.

### WP-230 — Retrieval and context packs

Implement reference resolution, related-entity traversal, source trace, ranking, token/size budgets, current-versus-historical state, and context-pack serialization.

**Depends on:** WP-100, WP-210.

## Wave 3 — Mutation and agents

### WP-300 — Transaction engine and audit

Implement proposal models, preconditions, dry-run, locks, atomic canonical apply, audit events, idempotency, projection staleness behavior, and recovery tests.

**Depends on:** WP-100, WP-200, WP-210, WP-220.

### WP-310 — Artifact and inbox lifecycle

Implement file registration, hashing, duplicate policies, manifests, quarantine, archive movement after successful transaction, and safe sidecar metadata.

**Depends on:** WP-200, WP-220, WP-300.

### WP-320 — Agent installer and session bootstrap

Detect Codex, Claude, and Gemini; generate project-scoped instruction/skill/MCP configuration safely; provide status and uninstall/repair behavior; open a context in a selected agent without managing user credentials.

**Depends on:** WP-120, WP-130. MCP config finalization also depends on WP-330.

### WP-330 — MCP server

Expose stable read and mutation tools, read-only resources, workflow prompts where useful, structured errors, context scoping, and integration tests with the official SDK.

**Depends on:** WP-120, WP-220, WP-230, WP-300.

## Wave 4 — Product workflows

### WP-400 — Tasks, claims, and generated operational views

Implement task operations, state history, parent/subtask integrity, waiting-on relationships, current focus, next actions, daily brief, and stale knowledge views.

**Depends on:** WP-110, WP-210, WP-300.

### WP-410 — Evidence-processing workflow contracts

Complete the deterministic tools and portable skills required for an agent to inspect an artifact, produce structured observations, resolve entities, propose updates, and persist an approved transaction. LLM extraction remains agent-driven; validation and persistence are deterministic.

**Depends on:** WP-230, WP-300, WP-310, WP-330.

### WP-420 — Drafting and outbox workflow

Implement context gathering and persistent drafts for messages, emails, status updates, and documentation. Sending/publishing remains out of scope except plugin interfaces and approval receipts.

**Depends on:** WP-230, WP-300, WP-400.

## Release wave

### WP-500 — End-to-end alpha, migration, docs, and packaging

- execute acceptance scenarios;
- migrate a sanitized fictional legacy fixture;
- verify agent adapters;
- build and install package artifacts;
- complete public docs;
- publish security and privacy guidance;
- create release notes and known limitations.

**Depends on:** all required Phase 1 packages.

## Suggested independent review assignments

- WP-100: reference-model reviewer;
- WP-200/WP-300: filesystem atomicity and concurrency reviewer;
- WP-220/WP-310: security reviewer;
- WP-330: MCP contract reviewer;
- WP-500: cross-platform and documentation reviewer.
