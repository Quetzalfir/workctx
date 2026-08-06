# Changelog

All notable changes will be documented here.

## 0.2.0-alpha - 2026-08-06

Second alpha: the complete Phase 2 (operational intelligence) and Phase 3
(external systems) scopes. See
[`docs/releases/0.2.0-alpha.md`](docs/releases/0.2.0-alpha.md) for details.
The workspace schema is unchanged; 0.1.0-alpha contexts need no migration.

### Added

- Six new generated views (resource directory, status report, people
  directory, glossary, agenda, suggestions) for eleven total.
- Secret references by name with environment-first then OS-keyring
  resolution and a `workctx secret` command group; values are never stored
  or displayed (ADR 0013).
- Personalization layers: user-level and context-level `instructions.md`
  merged into agent bridges at install, plus per-context skill overrides
  under `06_overrides/skills/` with three-way drift markers.
- Opt-in, local-only usage telemetry and an assisted improvement loop:
  `workctx usage status|evaluate|suggest` and
  `workctx suggestion list|show|adopt|reject`.
- Generic read-only connector runtime snapshotting external systems into
  quarantine-scanned inbox evidence, with per-snapshot schedules and
  due-state reporting (`workctx connector list|sync|status`).
- Browser-capture skill and guide for systems without API access.
- `workctx outbox send`: the first external write — preview-first,
  per-operation approval, duplicate-send fingerprinting, GitHub channel
  (ADR 0014).
- Machine-local context inventory:
  `workctx context register|list|unregister`.
- `workctx agent repair` and `workctx agent uninstall`; machine-readable
  `repair_action` in validation diagnostics.

### Changed

- Projection rebuilds are roughly 4x faster (fsync batching); batch
  `workctx inbox add` uses one lock and one projection refresh.
- `workctx agent install --agent all` targets detected clients and reports
  skipped ones instead of failing.
- Template-shipped bridge files are recognized by content hash so fresh
  contexts regenerate bridges and receive personalization.

### Fixed

- Suggestion records and their audit-ledger events share one clock,
  removing a wall-clock ordering flip-flop.
- `workctx context list` no longer renders one context as two rows when id
  and display name differ only by case.
- Stale documentation claims that drafting had "no send capability" were
  updated to describe the approval-gated send path.

## 0.1.0-alpha - 2026-08-03

First alpha: the complete Phase 1 scope (CLI and durable core). See
[`docs/releases/0.1.0-alpha.md`](docs/releases/0.1.0-alpha.md) for details,
known limitations, and the upgrade and stability policy.

### Added

- Isolated context workspaces created from a versioned template, with a
  reference system of stable IDs and canonical `workctx://` URIs.
- Canonical Markdown/YAML store with context locking, staged atomic writes,
  write-ahead intent records, and crash recovery.
- Workspace validation engine with stable diagnostic codes, strict mode, and
  repair guidance.
- Rebuildable SQLite/FTS projections with typed queries and full-text search.
- Retrieval engine: reference resolution, typed-relation traversal, evidence
  tracing to source locators, and deterministic budgeted context packs.
- Transaction engine: typed multi-entity proposals, validation and dry-run
  preview, explicitly approved atomic apply, and an append-only hash-chained
  audit ledger with verification and query commands.
- Inbox artifact lifecycle: registration with streaming hashes and manifests,
  secret and prompt-injection quarantine, duplicate detection, and post-commit
  archive to `01_processed/`.
- Generated operational views (current focus, next actions, waiting on, stale
  knowledge, daily brief) and the read-only `workctx brief` command.
- Evidence-processing workflow contracts and portable skills for repeated
  agent workflows.
- Local outbox drafting: approval-gated draft persistence to `05_outbox/` with
  no send capability.
- Stdio MCP server bound to one context, exposing the frozen version 1
  surface of 11 read tools and 6 approval-gated mutation tools.
- Agent adapter installers for Codex, Claude Code, and Gemini CLI with
  plan-first approval, drift detection, and repair.
- Deterministic legacy Markdown repository migration
  (`workctx migrate legacy`), preview-first, with safety findings and full
  reports; an apply is recorded as one audited import transaction.
- CLI envelope with exit bands and `--json` output across all commands.
- End-to-end acceptance suite driving the public CLI and MCP surfaces,
  including quarantine, approval-gate, and projection-rebuild scenarios.
- JSON Schemas for workspace and agent contracts, product and architecture
  documentation, and the multi-agent implementation plan.
