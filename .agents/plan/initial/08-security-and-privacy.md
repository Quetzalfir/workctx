# Security and privacy plan

## Trust model

Trusted:

- the `workctx` core release and verified dependencies;
- user-approved canonical configuration;
- explicit human approvals.

Untrusted by default:

- inbox artifacts;
- transcripts and copied chats;
- external-system responses;
- repository content from unknown sources;
- plugins;
- agent-generated proposals;
- worker reports and claims of successful validation.

## Main threats and controls

### Prompt injection in evidence

**Threat:** an artifact contains instructions to the agent, requests secrets, or attempts to override policy.

**Controls:**

- label evidence as untrusted data in skills and MCP resources;
- never execute embedded commands;
- separate extraction content from tool instructions;
- quarantine suspicious artifacts;
- require deterministic validation and approval for mutations;
- redact or block secret-looking values in proposals and logs.

### Cross-context leakage

**Threat:** a query, index, agent config, plugin, or cache exposes another company's data.

**Controls:**

- explicit `ContextHandle` on every operation;
- separate roots, databases, caches, plugin instances, and secret references;
- URI context enforcement;
- denial tests;
- no default federated search;
- context ID in audit and tool results.

### Path traversal and symlink escape

**Threat:** crafted paths read or write outside the context.

**Controls:**

- resolve and compare canonical paths;
- reject `..` escape;
- validate symlinks/reparse points;
- allowlist canonical roots;
- use safe temporary directories;
- test Windows junctions and POSIX symlinks.

### Secret disclosure

**Threat:** tokens appear in Markdown, logs, reports, archives, or model prompts.

**Controls:**

- secret references only;
- OS keyring/external manager adapters;
- detector in validation and commit hooks;
- redact command output and audit payloads;
- diagnostic bundles exclude content by default;
- separate read and write credentials by connector.

### Unsafe external writes

**Threat:** an agent sends a message, changes a ticket, publishes a document, or operates infrastructure without review.

**Controls:**

- external write tools are distinct from draft tools;
- default policy is `approval_required`;
- show exact target and payload;
- idempotency keys;
- receipt and audit event;
- narrow connector scopes;
- production operations remain outside core Phase 1.

### Malicious or compromised plugin

**Controls:**

- explicit capability and permission manifest;
- plugin allowlist per context;
- subprocess or process isolation considered for later phases;
- no plugin receives unrelated context or secrets;
- audit every plugin tool call;
- health and version reporting;
- clear uninstall and disable behavior.

### Audit tampering

**Controls:**

- append-only events;
- transaction IDs and content hashes;
- optionally hash-chain audit records;
- compare canonical Git history where available;
- never rely solely on mutable SQLite audit rows.

### Dependency and release supply chain

**Controls:**

- lock dependencies;
- dependency review and vulnerability scanning;
- signed releases when infrastructure exists;
- reproducible build instructions;
- minimal runtime dependency set;
- no install script that silently executes remote code beyond documented package-manager behavior.

## Data classification

Contexts should support classification labels such as:

- public;
- internal;
- confidential;
- restricted.

Classification influences:

- allowed models/providers;
- connector use;
- export and diagnostic behavior;
- plugin permissions;
- whether raw evidence may leave the machine.

Core Phase 1 stores policy metadata and enforces local boundaries; provider-specific data-governance guarantees remain the user's or organization's responsibility.

## Logging

Logs must contain IDs, operation names, timings, status, and sanitized errors—not raw evidence content by default. Debug content requires explicit opt-in and must still redact secrets.

## Security acceptance gate

Before alpha:

- threat-model review completed;
- traversal and cross-context tests pass on all supported operating systems;
- secret scanning enabled in CI;
- no external write is possible through core without explicit approval input;
- diagnostic export reviewed for data minimization;
- plugin interfaces documented as untrusted boundaries.
