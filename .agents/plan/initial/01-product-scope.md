# Product scope and requirements

## Primary users

### Architect profile

Needs broad understanding across repositories, services, infrastructure, security, people, requirements, decisions, and operational evidence. Writes less code than a full-time developer but must solve and communicate quickly.

### Developer profile

Uses AI for implementation, debugging, refactoring, testing, and review. Needs requirements and decisions from the work context plus precise code intelligence from optional tools.

### Hybrid profile

Combines architecture ownership, product decisions, implementation, operations, and communication.

### Light profile

Uses meetings, tasks, people, and drafts without indexing source code.

## Core use cases

### UC-001 — Process new evidence

A user adds a transcript, chat export, screenshot sidecar, document, note, or external-system snapshot. The system registers the artifact, extracts structured observations, resolves related entities, proposes a transaction, validates it, updates canonical state, rebuilds projections, and archives the original.

### UC-002 — Recover operational focus

A new session retrieves current priorities, blockers, waiting-on relationships, recent evidence, stale claims, and next actions without relying on prior chat history.

### UC-003 — Draft a contextual response

The user identifies a person or message. The system gathers the person's role, prior conversations, related work, latest evidence, commitments, and risks, then drafts a response without inventing commitments.

### UC-004 — Trace a conclusion

The user can move from a task or claim to the exact supporting observation and from there to a line, page, message, timestamp, image region, JSON pointer, or repository commit and line range.

### UC-005 — Manage related work

The system represents parent tasks, real subtasks, dependencies, blockers, owners, requesters, status history, due dates, and next actions. Generated views do not become competing sources of truth.

### UC-006 — Use a different AI agent

The user can close Codex, open Claude or Gemini in the same context, and continue through the same canonical state and MCP tools.

### UC-007 — Operate multiple contexts safely

The user can run separate company and project contexts on one computer without cross-context search, credentials, caches, or references.

## Functional requirements

| ID | Requirement |
| --- | --- |
| FR-001 | Create a context from a versioned template with a stable context ID. |
| FR-002 | Discover the active context from an explicit path or workspace root. |
| FR-003 | Register artifacts with SHA-256, media type, origin, event date, ingest date, and status. |
| FR-004 | Preserve raw artifacts and prevent duplicate ingestion by content hash and policy. |
| FR-005 | Represent evidence notes, atomic observations, claims, tasks, people, teams, systems, flows, decisions, risks, and questions. |
| FR-006 | Resolve stable canonical URIs and typed relationships. |
| FR-007 | Store precise source locators for every material observation. |
| FR-008 | Build a context pack around an entity using direct, typed, time-aware relationships. |
| FR-009 | Search canonical text and structured metadata locally. |
| FR-010 | Apply multi-entity changes as validated atomic transactions. |
| FR-011 | Generate operational views from canonical entities. |
| FR-012 | Validate schema, references, IDs, hierarchy, paths, isolation, and generated-state freshness. |
| FR-013 | Rebuild all derived indexes and views from canonical data. |
| FR-014 | Expose core operations through CLI and MCP. |
| FR-015 | Install or generate agent-specific adapters without duplicating canonical rules. |
| FR-016 | Produce machine-readable JSON output for automation in addition to human output. |
| FR-017 | Record append-only audit events for mutations and external actions. |
| FR-018 | Require approval for external writes unless a context policy explicitly permits a narrower action. |
| FR-019 | Support dry-run and review-before-apply workflows. |
| FR-020 | Export a diagnostic bundle that excludes raw evidence and secrets by default. |

## Non-functional requirements

| ID | Requirement |
| --- | --- |
| NFR-001 | Windows, macOS, and Linux support. |
| NFR-002 | No network dependency for core context creation, validation, search, and retrieval. |
| NFR-003 | Deterministic validation and transaction behavior. |
| NFR-004 | Human-readable recovery when generated state is deleted. |
| NFR-005 | Clear exit codes and JSON output for scripting. |
| NFR-006 | No secret values in workspace files or logs. |
| NFR-007 | Cross-context access denied by default. |
| NFR-008 | Backward-compatible migrations for released workspace schemas. |
| NFR-009 | Useful performance for at least 100,000 canonical documents on a modern developer laptop; exact benchmarks must be established before release. |
| NFR-010 | Core domain and transaction behavior covered by high-confidence automated tests. |

## Phase 1 user workflow

```text
install -> context init -> agent install -> add evidence -> process/propose
-> review/apply -> search/brief/draft -> validate -> close session
```

## Explicitly deferred

- background cloud workers;
- real-time collaboration;
- browser-based visual graph;
- native audio/video diarization pipeline;
- automatic connector discovery;
- enterprise RBAC and central policy;
- autonomous production operations.
