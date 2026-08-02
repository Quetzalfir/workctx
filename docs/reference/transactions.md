# Transactions and audit ledger

The transaction engine is the only application API for a validated multi-file canonical
mutation. It turns a typed proposal into an ordered, recoverable filesystem intent and one
tamper-evident audit event. SQLite remains a rebuildable projection and never determines
whether canonical work committed.

## Public API

`workctx.transactions` exports:

- `validate_proposal(root, proposal)` for read-only proposal, revision, condition, document,
  and reference checks;
- `dry_run(root, proposal)` for the same checks plus exact ordered effects and content hashes,
  without acquiring a lock or writing canonical or derived state;
- `apply(root, proposal, approved=..., session_id=...)` for the ADR 0006 commit protocol;
- `recover(root, strategy, transaction_id=...)` for ledger-event-gated cleanup or rollback of
  an interrupted intent;
- `verify_ledger(root)` and `audit_summary(root)` for verified audit reads.

`TransactionEngine` exposes the same operations for callers that need dependency injection in
tests. Public receipts and diagnostics are Pydantic records in
`workctx.transactions.models`. Expected failures use the typed exceptions in
`workctx.transactions.errors`; their messages and diagnostics never contain proposal values.

Callers construct `TransactionProposal` from `workctx.domain.transactions`. The proposal is a
closed version-1 object containing:

- a `TXP-YYYYMMDDTHHMMSSZ-slug` ID whose timestamp equals `created_at`;
- one context ID and a 64-character lowercase-hex base revision;
- a discriminated human, agent, or system actor;
- unique durable source references;
- one or more ordered `create`, `update`, `move`, or `delete_generated` operations;
- unique typed preconditions and postconditions;
- exactly `expected_views: [sqlite]`;
- `approval: required` or `not_required`.

Create and update payloads are discriminated typed documents: entity, task, claim, standalone
observation, or artifact manifest. Update, move, and generated-delete operations require an
exact `sha256:<64-lowercase-hex>` preimage hash. Creates require an absent target. Move sources
must exist, destinations must be absent, and all target parents must already exist. Only files
under `04_views/` can use `delete_generated`. The audit ledger cannot be addressed by a user
operation.

Proposal `approval` states what the producer expects; it does not grant authority. When it is
`required`, `apply(..., approved=True)` is the authoritative runtime approval signal.

## Validation and dry-run

Models and the JSON Schema contracts reject malformed actors, paths, operations, payloads,
conditions, hashes, references, timestamp/ID mismatches, cross-context local references, and
normalized path collisions. Relations that JSON Schema cannot express are disclosed as
producer invariants and enforced by the domain models, following ADR 0011.

The engine then composes D-025 checks in memory:

1. verify the entire ledger and derive its current head;
2. reject a duplicate proposal ID or stale base revision;
3. require a ready read-only SQLite projection whose indexed identities still match canonical
   inputs;
4. refuse secret-looking proposal values with location-only diagnostics;
5. serialize each typed document through the canonical filesystem APIs;
6. compare exact target state and preimage hashes;
7. resolve proposed identities from the in-memory overlay before querying the projection;
8. traverse every typed local-reference carrier in the proposal, including evidence extra
   fields, embedded observation identities and references, task raw-ID dependencies and
   blockers, task URI blockers, entity/claim/observation relations, and Markdown body
   references;
9. evaluate path and reference conditions against the initial or final overlay as appropriate;
10. report canonicalization of a hand-edited update as a warning.

Preflight resolves those reference carriers against the complete proposed overlay and current
canonical identities, so cross-referenced documents can be created together without a
temporary write. It also rejects identity collisions within the staged overlay or against
current canonical identities, including collisions involving embedded observation identities.
Only consistency that requires the materialized workspace as a whole and cannot be proved from
the proposal overlay and current projection remains the responsibility of the strict
`validate_workspace` post-apply gate, including global graph and cycle consistency. This is the
D-025 split: every enumerated proposal carrier and staged identity is checked before intent
publication, while the existing workspace validator is still the authoritative final
postcondition after canonical replacements.

External source references are syntax-validated. Local `workctx://` references resolve against
the proposed overlay first and then `SQLiteProjection.get_document_by_uri`. Artifact references
resolve against proposed or existing manifests. A `reference_exists` condition for a reference
family that cannot be verified locally fails closed.

Dry-run effects are ordered and contain the operation, source/target path, optional move
destination, exact preimage and postimage hashes, and the hand-edit flag. A move lists the same
content hash as its preimage and postimage; a delete has no postimage. A not-ready projection
is reported as a validation error rather than being rebuilt, preserving the no-write contract.

## Context revision

The context revision is the bare 64-character lowercase hexadecimal `event_hash` of the last
verified ledger event. An empty ledger has the genesis revision of 64 zero characters. The
engine rereads the ledger under the context lock and requires `proposal.base_revision` to equal
that head. A rolled-back transaction still appends an audit event and therefore advances the
revision: the decision and recovery history are canonical state even when document preimages
were restored.

Duplicate identity and stale revision have distinct recoverable conflict codes:
`TXN-DUPLICATE-PROPOSAL` and `TXN-STALE-REVISION`. Duplicate detection takes precedence when a
caller retries the exact already-recorded proposal.

## Apply sequence

`apply` composes only the public WP-200/WP-201 filesystem primitives:

1. acquire `ContextLock` and require recovery state `clean`;
2. verify the ledger, duplicate ID, current revision, and operation preconditions under the
   lock;
3. rebuild the projection to make D-025 queries correspond to current canonical inputs, then
   validate and serialize the complete proposed set in memory;
4. verify the lock fence;
5. call `StagedReplacement.prepare` to fsync postimages and preimage backups and publish the
   write-ahead intent before mutation;
6. call `StagedReplacement.apply`, which fences before the first operation and every bounded
   retry;
7. run `validate_workspace(strict=True)` as the post-apply postcondition gate while the intent
   remains durable;
8. append exactly one sealed audit event with `atomic_append_line_bytes`, whose final retry
   check fences the lock and verifies the ledger preimage;
9. reverify the exact ledger event and call the matching post-audit intent finalizer;
10. rebuild SQLite, release the lock, and return the complete receipt.

The `ApplyResult` interface contains the proposal and context IDs, base and committed revisions,
all affected paths (a move includes source and destination), ledger event ID and hash, ledger
source references, and typed projection status.

If full workspace validation fails after the operations, the engine restores all preimages,
appends a `rolled_back` event, verifies it, calls the rollback finalizer, and raises
`PostconditionRollbackError` with its durable `RecoveryResult`. A failure after intent
publication but before verified finalization raises `RecoveryPendingError`; the intent and
recovery assets remain authoritative.

## Ledger representation and verification

The canonical ledger is `99_meta/audit/ledger.jsonl`. The first fenced append creates the
missing `99_meta/audit/` directory through the WP-201 primitive. Each event is compact UTF-8
JSON followed by exactly one LF. Blank lines, carriage returns, a BOM, duplicate JSON keys,
noncanonical bytes, incomplete final lines, and duplicate event or proposal IDs are invalid.

Events contain only actor metadata, source references, operation names and paths, and content
hashes; they never contain document payloads or secret values. An event has:

- an `AUD-...` ID derived exactly from its `TXP-...` proposal ID;
- `action: apply` or `recovery` and `result: committed` or `rolled_back`;
- `base_revision` and `prev_hash`, which are equal to the previous ledger head;
- typed audit operations with preimage/postimage hashes;
- `event_hash`, the SHA-256 digest of ADR 0005 compact canonical JSON for the same event with
  `event_hash` replaced by the empty string.

The first event uses 64 zero characters for `prev_hash`. Verification parses and reseals every
event in order, checks the bound context, canonical line bytes, unique identities, every hash,
and every chain link. Any mismatch raises `LedgerIntegrityError`; no transaction or recovery
mutation proceeds with a ledger that does not verify. Git history over `99_meta` remains the
independent backstop described by ADR 0010.

For `action: apply`, actor and source references come from the authenticated proposal, while
the ordered operations match both that proposal and its durable intent. `action: recovery` has
one narrower meaning: it is a hash-verified preimage rollback for an eventless intent. Such an
event must have
`result: rolled_back`, the reserved `workctx-transaction-recovery` system actor, empty
`source_refs`, and the ordered operations reconstructed from the intent. It does not claim
proposal actor or source provenance that the intent does not retain. Cleanup after a matching
verified event appends no second event.

Append is idempotent only for an exact existing event. If an append primitive reports an
ambiguous failure, the ledger is reread: the operation succeeds only when the exact sealed event
is present. Reusing either the event ID or proposal ID for different bytes is an integrity
failure.

## Recovery

D-031 makes recovery ledger-event-gated. The verified ADR 0010 append is the commit point
because it occurs after every canonical replacement and before intent finalization. Recovery
therefore never forward-completes staged replacements and never attempts to reconstruct actor,
source, or condition data that the intent does not retain.

Under a successor lock, recovery verifies the complete ledger and the active intent before it
does anything:

- an intent with its exact matching event anywhere in the fully verified ledger chain is
  already committed or rolled back; later valid events do not erase that commit proof.
  Recovery checks that the event operations match the intent, then performs only the matching
  cleanup/finalization and projection refresh, without another canonical replacement or ledger
  append;
- an intent with no event is uncommitted; recovery restores only its hash-verified preimages,
  appends one `recovery`/`rolled_back` event, verifies it, and calls the post-audit rollback
  finalizer;
- invalid intents, ledger mismatches, wrong transaction selectors, and target or recovery-asset
  conflicts fail closed without cleanup.

Those rules apply regardless of the caller's requested `complete` or `rollback` strategy. A
requested `complete` for an eventless intent returns a `rolled_back` outcome; the receipt still
records the requested strategy. Retrying the intended change requires a newly issued typed
proposal with a new ID and the advanced ledger revision to go through the full authenticated
`apply` path, including validation, actor, approval, and conditions. Calling recovery again
with `transaction_id` after cleanup returns an `already_finalized` receipt only when the
selector is known in the verified ledger.

## Projection failure after commit

Projection refresh occurs only after audit verification and intent finalization. If refresh
raises or skips canonical documents, the engine does not roll back or erase the committed
transaction. It calls `SQLiteProjection.invalidate()`, checks read-only readiness when possible,
and returns `projection.state: stale`, `TXN-PROJECTION-STALE`, invalidation confirmation, and
the `workctx index rebuild` repair instruction. Canonical document and ledger receipt fields remain
complete and authoritative regardless of derived-state failure.
