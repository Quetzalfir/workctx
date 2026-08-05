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
- first-class projections for everyday work data: resource directory,
  people and teams directory, glossary, agenda and deadlines, and a status
  report generator — all generated views over existing canonical entities;
- context dashboard;
- drag-and-drop inbox;
- transaction review queue;
- tasks, people, knowledge, outbox, and audit views;
- launch and headless-agent controls.

## Phase 3 — Plugins and connectors

- a generic declarative connector runtime: one snapshot engine driven by
  per-source manifests (endpoint, schedule, secret reference), so any
  service — including private ones — can feed evidence without bespoke code;
- thin named adapters on top of the runtime where a source needs real logic
  (GitHub, Jira, Confluence, Dynatrace, Rally, Teams, email — built on
  demand, not by catalog);
- code-index and graph tools (Sourcegraph, CodeQL, LSIF/SCIP exporters, or
  any similar tool) integrate generically: their exports enter as ordinary
  evidence or connector manifests, and observations anchor to code with
  repo:// locators — no bespoke adapter unless proven need;
- an Obsidian companion guide: a context opens directly as a vault for
  visual browsing and the entity graph, with workctx remaining the only
  mutation path;
- scheduled connector synchronization;
- one approval-gated outbox send channel at a time (draft -> preview ->
  per-operation approval -> audited delivery), GitHub first.

## Phase 4 — Team and enterprise capabilities

- remote multi-user deployment;
- authentication, authorization, policy, and governance;
- shared contexts and conflict resolution;
- optional temporal graph and advanced retrieval.
