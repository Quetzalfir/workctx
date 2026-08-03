# Security and privacy

This page describes the security model of the first alpha: what workctx trusts, how evidence is contained, which operations require approval, how secrets are handled, and — just as important — what workctx does not protect against.

## Trust model

Trusted:

- the workctx release you installed and its verified dependencies;
- the canonical configuration you wrote or approved;
- explicit human approvals given at the command line or through an MCP client.

Untrusted by default:

- everything in the inbox: transcripts, chats, documents, screenshots, exports;
- responses from external systems;
- repository content from unknown sources;
- agent-generated proposals, reports, and claims of successful validation.

Evidence is data, never instructions. No workctx component executes, renders, or follows content because it appeared inside an artifact, and skills and MCP resources label evidence as untrusted so agents inherit the same rule.

## Evidence quarantine

Registration is the only door into a context. `workctx inbox add` streams the file through SHA-256, records a manifest with source metadata, and scans the bytes for two classes of dangerous content:

- **possible secrets** — credential-shaped values such as tokens, private keys, and password assignments;
- **instruction-like content** — text that addresses an agent directly, attempts to override policy, or requests secrets (prompt injection).

A flagged artifact is quarantined under `00_inbox/quarantine/` together with its sidecars. Quarantined bytes are never parsed, decompressed, rendered, executed, or copied into proposals, reports, views, or diagnostics; quarantine diagnostics contain a stable reason and a location, never the matched value or a raw excerpt. Quarantine review is a human decision.

The scanner is heuristic: it narrows the attack surface, it does not eliminate it. See [inbox lifecycle](reference/inbox.md) for the exact semantics.

## Approval gates

Every canonical mutation requires an explicit approval signal at the moment of the operation:

- the CLI previews by default; `workctx transaction apply` mutates only with `--yes`, and `workctx agent install` executes its plan only with `--yes`;
- every MCP mutation tool requires `"approved": true` in its input and fails with `APPROVAL_REQUIRED` without it ([ADR 0012](adr/0012-mcp-tool-surface-alpha.md));
- an approved apply is atomic: all affected files change under the context lock or none do, and one audit event records the mutation.

The alpha contains **no external-write capability at all**. There is no tool that sends a message, posts, publishes, uploads, or changes an external system. Drafting persists local files under `05_outbox/` and has no send, mail, network, or connector primitive; saving a draft can never become a delivery ([drafting reference](reference/drafting.md)). Any future connector will be a separate, explicitly approved external-write operation.

## Secret policy

Secret values are never stored. Workspaces hold secret references at most — names that resolve through an external secret manager — and never the values themselves.

Detection is refusal, not redaction-and-continue, at every write boundary:

- inbox registration quarantines artifacts containing credential-shaped bytes;
- workspace validation rejects canonical files containing possible secrets;
- draft saving refuses the whole save when any payload field looks like a secret, with a content-free error;
- audit events record actor metadata, paths, and content hashes — never document payloads or secret values;
- MCP tool results and resources are sanitized before crossing the boundary: secret-looking assignments, bearer tokens, and private-key material are redacted, and unexpected server errors return a bounded code instead of a traceback.

## Context isolation

Each company or project context is a separate security boundary:

- separate directory tree, SQLite projection, caches, and audit ledger per context;
- every operation is bound to exactly one context; `workctx://` references that cross the boundary are rejected with `REF-CONTEXT-MISMATCH`, even when nested inside a transaction proposal;
- absolute filesystem paths, `..` traversal, and symbolic-link or junction escapes are rejected on canonical reads and writes;
- one MCP server process serves exactly one context root and cannot address another context;
- there is no federated search across contexts.

Run one agent session per context. See [multiple contexts](guides/multiple-contexts.md) for the operational guidance.

## Integrity and audit

- One writer per context: canonical writes require the context lock, with stale-lock takeover and fencing so an interrupted or superseded writer cannot corrupt state ([ADR 0006](adr/0006-context-locking-and-atomic-writes.md)).
- Writes are staged and applied atomically with a write-ahead intent record, so an interruption at any point is detectable and repairable.
- The audit ledger (`99_meta/audit/ledger.jsonl`) is append-only and hash-chained: each event carries the hash of the previous one, and verification replays the chain ([ADR 0010](adr/0010-audit-ledger-representation.md)). Keeping the context in Git adds an independent history of the same file.
- SQLite indexes and generated views are disposable projections; deleting and rebuilding them is a supported, tested path and loses nothing.

## What workctx does not protect against

Be explicit about the residual risks; workctx is a local tool, not a security product:

- **An attacker with filesystem access.** Anyone who can write to the context directory can alter canonical files and rewrite the entire audit chain. The hash chain makes casual tampering evident; it does not stop a capable local attacker. Git history and filesystem permissions are your mitigations.
- **No encryption at rest.** Contexts are plain files. If the disk is shared or portable, use full-disk or directory-level encryption from your platform.
- **Your model provider.** workctx cannot control what an agent or hosted model does with content you choose to send it. Deciding which contexts may be exposed to which providers remains your responsibility.
- **Agent behavior outside the tool boundary.** An agent with shell access can bypass workctx entirely. The approval gates govern the workctx surfaces; they do not sandbox the agent process itself.
- **Detection gaps.** Secret and prompt-injection scanning is heuristic. Novel encodings or formats can pass; review of evidence and proposals remains necessary.
- **Network filesystems.** Locking and atomicity guarantees are stated for local NTFS, APFS, and ext4. SMB or NFS mounts receive no additional guarantees in this alpha.
- **Denial of service.** Nothing prevents a local process from deleting files; back up contexts like any other important directory.

## Reporting

Report suspected vulnerabilities through the process in [`SECURITY.md`](../SECURITY.md); do not open public issues for security reports.
