# Changelog

All notable changes will be documented here.

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
