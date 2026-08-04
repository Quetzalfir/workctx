# ADR 0013 — Machine-global secret references with env-first keyring resolution

- Status: accepted
- Date: 2026-08-03
- Deciders: implementation lead, ratifying the WP-620 design; operator directed
  the feature pull-forward from Phase 3

## Context

Secret values must never enter workspaces, source control, envelopes,
diagnostics, logs, reports, or agent transcripts — a founding product
invariant that the validation engine, inbox quarantine, and draft refusal
already enforce against VALUES. What was missing is the affirmative path:
how canonical files may name a secret and how that name resolves at use time.
`keyring` provides portable local credential-store access (Windows Credential
Manager, macOS Keychain, Secret Service) but cannot enumerate entries, and CI
or minimal environments may lack a usable backend.

## Decision

1. Canonical files store validated names through `secret_ref` — lowercase
   kebab, 1-64 characters — never values.
2. Names are machine-global in v1; per-context keyring scoping is
   deliberately deferred. Project-prefixed names are the v1 collision
   mitigation.
3. Resolution checks the documented environment variable
   (`WORKCTX_SECRET_<UPPER_SNAKE_NAME>`) first, then keyring service
   namespace `workctx`.
4. Resolved text is returned only inside an opaque `SecretValue`;
   `reveal()` is the sole value accessor. String conversion, representation,
   formatting, and JSON/Pydantic serialization redact; pickling raises.
5. Keyring is authoritative for stored values. Because keyring cannot
   enumerate, a platformdirs user-config file stores sorted NAMES only,
   updated under a cross-process lock with atomic replacement.
6. When keyring or its backend is unavailable, env-resolvable references
   keep working; OS-store operations fail with a content-free diagnostic in
   the unavailable-dependency exit band; listing reports OS presence as
   unknown with a warning.

## Consequences

- Environment entries intentionally shadow OS-store entries.
- The names index can go stale and is never authoritative; repeating
  `secret set` repairs it, and exact-name resolution works regardless.
- OS-store mutation and index replacement are not one transaction; the
  failure mode (stored but unindexed) is benign and self-healing.
- `secret import` deletion of a source dotenv file is best effort on
  copy-on-write filesystems, SSDs, backups, and synchronized storage.
- Plaintext workspace storage was evaluated and rejected, including for
  local-only development; `secret import` exists so the ergonomic gap is
  zero. External secret managers and per-context scoping remain future
  decisions.
