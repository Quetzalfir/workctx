# Roadmap

## Phase 1 — CLI and durable core

- context creation and isolation — done;
- canonical schemas and reference resolver — done;
- artifact registration and evidence processing transactions — done;
- validation, indexing, search, and generated operational views — done;
- outbox drafting with the no-send boundary — done;
- portable skills and agent installers — done;
- local MCP server — done;
- legacy Markdown repository migration — done;
- multi-agent development and review workflow — done;
- public documentation and packaging — this release (`0.1.0-alpha`).

Post-alpha within Phase 1: incremental projection updates (today the SQLite
index and generated views are rebuild-only).

## Phase 2 — Assistant experience and local UI

- portable personalization: user-level and per-context instruction layers
  (tone, role, boundaries) merged into every installed agent adapter, so
  preferences are written once and reach Codex, Claude Code, and Gemini alike;
- assisted improvement loop: the agent detects quality drift (missing
  references, stale structure, weak output contracts), files each finding as a
  reviewable suggestion, and adopted suggestions become versioned local skill
  or template changes that survive upgrades — engine changes remain ordinary
  open-source contributions to this repository;
- context dashboard;
- drag-and-drop inbox;
- transaction review queue;
- tasks, people, knowledge, outbox, and audit views;
- launch and headless-agent controls.

## Phase 3 — Plugins and connectors

- Graphify and CodeGraph adapters;
- GitHub, Jira, Confluence, Dynatrace, Rally, Teams, and email connectors;
- approval workflows and background synchronization.

## Phase 4 — Team and enterprise capabilities

- remote multi-user deployment;
- authentication, authorization, policy, and governance;
- shared contexts and conflict resolution;
- optional temporal graph and advanced retrieval.
