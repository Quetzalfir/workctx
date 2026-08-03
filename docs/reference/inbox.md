# Artifact and inbox lifecycle

The ingestion package registers local files that are already present under
`00_inbox/raw/`. It never fetches evidence, executes it, renders it, or treats text inside it
as agent instructions. SHA-256 content identity is exposed as
`artifact://sha256/<64-lowercase-hex>` and remains stable when the preserved original moves.

## Public application API

`workctx.ingestion` exports these typed operations:

- `register(context_root, RegisterRequest(...)) -> RegistrationResult`;
- `list_inbox(context_root, statuses=...) -> InboxListing`;
- `quarantine_info(context_root, artifact_id) -> QuarantineInfo`;
- `archive_after(context_root, artifact_id, receipt) -> ArchiveResult`.

`IngestionService` provides the same methods for callers that need an injected clock,
transaction function, policy, or WP-201 stager in tests. Paths in `RegisterRequest` are
context-relative portable POSIX paths below `00_inbox/raw/`. Absolute paths, traversal,
backslashes, links escaping the context, and non-regular files fail closed.

The caller supplies source type and optional source metadata. Media type is inferred from a
closed, cross-platform suffix table unless it is supplied explicitly; an unknown suffix,
unsupported media type, or mismatch between a declared type and the known suffix is
quarantined rather than trusted.

## Registration and duplicate policy

Registration streams the complete primary file through SHA-256 and writes a schema-valid JSON
manifest to `00_inbox/manifests/<ART-ID>.json`. The manifest create is the only WP-300 proposal
payload: it contains metadata, paths, and hashes, never evidence bytes. Artifact IDs use the
UTC ingest date, a portable filename slug, and the first free two-digit sequence.

Re-registering the same unchanged live path is idempotent and returns `already_registered`
without another ledger event. For the same content at another path, the request selects one of
two policies:

- `refuse` raises `DuplicateArtifactError` and creates no second manifest;
- `link` retains the second original and creates a manifest with `status: duplicate` and
  `duplicate_of` set to the first deterministic manifest match.

Same-name files with different hashes receive distinct IDs. A security quarantine takes
precedence over duplicate handling so an executable or otherwise suspicious new path is not
left unclassified merely because its bytes were seen before.

Optional sidecars are registered as an ordered set in the manifest. Ingestion stores their
source paths and streaming hashes in the manifest's versioned, compact `notes` metadata so a
move can verify and recover the complete set. The metadata also retains the original raw path
after `preserved_path` changes. It contains no evidence content.

## Bounded quarantine guards

The default size limit is 100 MiB per primary file or sidecar. The limit is configurable with
`IngestionPolicy`; exceeding it is a quarantine reason, not a reason to skip hashing. Hashing
and scanning use 1 MiB chunks by default and retain only a 4 KiB overlap for markers spanning a
chunk boundary.

The deterministic guards flag:

- prompt-injection markers, including attempts to override prior/system/developer
  instructions or reveal protected prompts and secrets;
- possible secrets through the public `workctx.validation.contains_possible_secret`
  predicate;
- executable suffixes, executable media types, shebangs, and common PE/ELF/Mach-O magic;
- unsupported or mismatched media types;
- files over the configured size limit.

Diagnostics contain only a stable reason and context-relative location. They never contain a
matched value or raw excerpt. The scanner treats bytes as untrusted data and performs no
document parsing, decompression, macro inspection, rendering, or command execution.

For a suspicious registration, WP-300 first commits the small quarantined manifest. WP-310
then authenticates the resulting receipt against the complete ledger and uses a WP-201 staged
move under the context lock to move the primary and all sidecars into
`00_inbox/quarantine/`. The manifest retains an authenticated, content-free registration
receipt in its versioned `notes` metadata so an interrupted quarantine move can be resumed by
re-running the same `register` request. `quarantine_info` reports reasons and physical/recovery
state without opening the evidence.

## Archive after commit

`archive_after` requires a supplied `ApplyResult`. It performs these steps in order:

1. call `workctx.transactions.authenticate_apply_result` and use only the returned canonical
   event as proof;
2. require that event's exact `source_refs` to contain the artifact URI;
3. verify the live primary and sidecar hashes;
4. commit one WP-300 manifest-only update to `status: processed` and `01_processed/`
   locations when the caller's transaction has not already made that state change;
5. acquire the context lock and authenticate the supplied receipt again;
6. execute only physical WP-201 staged moves, then finalize their intent against the
   authenticated audit proof.

A missing, forged, foreign-context, rolled-back, ledger-tampered, or nonreferencing receipt
causes no manifest transition and no move. Caller-controlled receipt fields that are not bound
by authentication are never used as authority. The archive manifest proposal contains no move
operation and no evidence payload.

Archive destinations are deterministic root-level names derived from the artifact ID and a
bounded suffix. This avoids parent-directory creation during the move, prevents name
collisions, and keeps Windows paths bounded while `original_name` preserves source naming.

## Idempotency and recovery

Both physical transitions use `StagedReplacement` with `StagedMove` values. WP-201 streams
preimage backups, fsyncs the intent before the first replace, fences every mutation with the
context-lock nonce, and retries Windows sharing violations with bounded backoff.

If interruption leaves a `WP310-quarantine-...` or `WP310-archive-...` intent, retry the same
ingestion operation with the same archive receipt where applicable. The service authenticates
the receipt, verifies that every intent path and hash exactly matches the manifest metadata,
completes only that intent, and finalizes it. An unrelated, malformed, or conflicting intent is
never adopted. If all destinations already contain the registered hashes and all sources are
absent, a retry returns the idempotent completed result without another transaction.

The generic WP-300 recovery command does not own these physical-only intents: their audited
WP-300 event records the manifest transition by D-036, while their WP-201 intent deliberately
records the separate evidence move. Recovery therefore happens through the matching ingestion
operation.

## Operational constraints

- Core ingestion accepts local files only; placing bytes into `00_inbox/raw/` is an explicit
  operator or adapter action outside this API.
- Do not modify a registered raw file or sidecar in place. A changed live path fails as an
  inconsistent lifecycle state instead of silently rewriting its immutable hash.
- Evidence zones are opaque to workspace-wide content validation. Registration is the bounded
  security scan and manifests remain ordinary validated canonical JSON.
- Quarantine is conservative triage, not a claim that content is malicious. Review happens in
  a separately authorized workflow that still treats the artifact as untrusted data.
