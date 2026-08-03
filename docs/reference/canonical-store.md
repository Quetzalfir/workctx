# Canonical filesystem store

The filesystem adapter is the only low-level API for reading and replacing canonical Work
Context documents. It binds every operation to one resolved context root, validates typed
domain models, enforces workspace zones, and applies the serialization, locking, and staging
decisions in ADRs 0005, 0006, and 0009.

## Canonical serialization

`workctx.adapters.filesystem.serialization` emits UTF-8 without a BOM and uses LF line
endings. YAML uses PyYAML with these pinned arguments:

```python
sort_keys = False
allow_unicode = True
default_flow_style = False
indent = 2
width = 4096
```

Declared Pydantic fields retain model declaration order. Extra model fields and free-form
mappings are sorted lexicographically, recursively. `model_dump(mode="json")` controls
null emission: schema-nullable declared fields remain present as `null`, while the integrated
domain serializers omit absent nested fields that the public schema makes non-nullable.
Raw model input is checked before dumping: mapping keys must be strings and free-form values
must already be finite, JSON-native data. Sets, iterators, generators, bytes, non-finite
numbers, and other values whose coercion could be lossy or process-dependent are rejected.

Markdown documents start with `---`, contain canonical YAML frontmatter, and place exactly
one blank line between the closing delimiter and the normalized body. Reads use
`workctx.domain.frontmatter.parse_frontmatter`; there is no adapter-specific frontmatter
parser. The hand-edit checks parse and validate a document, reserialize it canonically, and
compare the exact original bytes. Invalid documents raise instead of being mislabeled as
ordinary byte drift.

## Typed `CanonicalStore`

`CanonicalStore(root)` accepts an already selected context root; it never performs ancestor
or registry discovery. Its typed APIs are:

- `read_context_config`, `prepare_context_config`, and `write_context_config`;
- `read_entity`, `prepare_entity`, and `write_entity` for Markdown under `02_knowledge/`,
  `03_work/`, or `05_outbox/`;
- `read_task`, `prepare_task`, and `write_task` for Markdown under `03_work/`;
- `read_artifact_manifest`, `prepare_artifact_manifest`, and `write_artifact_manifest` under
  `00_inbox/manifests/`;
- per-document `*_has_hand_edits` checks.

Artifact manifests support `.yaml`, `.yml`, and `.json`. This preserves the architecture's
explicit YAML-or-JSON allowance while keeping both forms byte deterministic. Artifact
preserved-file and sidecar paths must remain below `00_inbox/` or `01_processed/`.

`prepare_*` returns a `StagedWrite` without changing canonical state, so the transaction layer
can combine typed documents into one intent. A direct `write_*` is a safe single-file
operation: it acquires a context lock unless the caller supplies its held lock, writes and
fsyncs a temporary file under `98_state/staging/`, fences, and calls `os.replace`. Context
initialization is the bootstrap exception: its private context-config writer uses the same
canonical serializer after the template has been copied.

Every path is context-relative. Absolute, drive-relative, rooted, empty, dot-segment,
wrong-zone, wrong-suffix, nested-context, and resolved symlink or junction escapes are rejected.
Entity and task `workctx://` URIs must also name the bound context. Core reads use
open-read-close so they do not retain handles that interfere with Windows replacement.

## Lock protocol

Acquire a lease with `ContextLock.acquire(root, session_id=...)`. `98_state/lock.json` is
created using exclusive-create semantics and contains exactly:

```text
pid, hostname, session_id, tool_version, acquired_at, heartbeat_at, nonce
```

The nonce is random 128-bit lowercase hexadecimal identity. Holding an old Python object is
not ownership; `verify_fence()` and `verify_lock_fence()` re-read `lock.json` and require the
expected nonce.

`heartbeat()` writes and fsyncs a complete `lock.json.tmp`, fences again immediately before
`os.replace`, and changes only `heartbeat_at`. A valid lock is stale when its heartbeat is
older than the configured threshold (ten minutes by default), or when its PID is definitely
absent on the same host. Remote PIDs are never probed. On Windows, PID inspection uses process
handles rather than `os.kill`.

All mutations of `lock.json` also use transient, exclusive-create choosing and ticket files
under `98_state/staging/`. A file-only bakery protocol serializes acquisition and takeover, the
heartbeat fence/temp-write/replace sequence, and the final release fence and unlink. Every
claim path contains a random nonce and is never reused, so dead-owner cleanup cannot unlink a
successor's claim. Guard age never invalidates parseable ownership: a claim is reclaimed only
when a same-host PID is definitely absent. A malformed unique claim is conservatively
recoverable after one hour. If Windows sharing prevents removal after bounded retries, a unique
per-artifact `cancelled` marker retires that claim. Later acquisitions retry removal of the
unique claim and then its marker, so restored filesystem availability reaps both safely. A
partial or mismatched cancellation left by a crash fails closed and becomes conservatively
recoverable after one hour.

Malformed lock content uses only file mtime: it is stale strictly after the same threshold.
Takeover first rechecks the observed bytes and file identity, then uses a no-overwrite hard-link
claim followed by unlink to move the evidence to
`lock.stale-YYYYMMDDTHHMMSS.ffffffZ.json`. A numeric suffix preserves every collision instead of
overwriting an existing archive. Takeover never removes staging content. Heartbeat, release,
and commit fencing by an old holder fail without changing its successor's lock.

The protocol is designed for local NTFS, APFS, and ext4 filesystems. Phase 1 adds no stronger
guarantee for SMB or NFS.

## Staged operations and intent lifecycle

`StagedReplacement.prepare(transaction_id, nonce, writes, lock=holder)` accepts an ordered
mixture of `StagedWrite`, `StagedMove`, and `StagedDelete` values. The class name and parameter
name remain unchanged for compatibility. Preparation performs these steps:

1. validate context-bound paths and reject any case-folded or Unicode-normalized collision
   among replacement targets, move sources and destinations, and delete targets;
2. require every move source and delete target to exist as a regular file, require every move
   destination to be absent, and require all operation parents to exist on the staging volume;
3. snapshot, flush, fsync, hash, and retain each existing preimage in the transaction directory;
4. write, flush, fsync, and hash a staged postimage for every replacement; moves use the source
   itself as the forward postimage and deletes have no postimage file;
5. verify that the supplied `ContextLock` belongs to the context and owns the intent nonce;
6. fsync and publish the fixed write-ahead `intent.json` before applying any operation.

Intent records remain at `schema_version: 1`. A target object containing exactly the original
five fields (`target`, `staged`, `content_hash`, `backup`, and `preimage_hash`) is always read as
a replacement, and new replacement records continue to use that legacy shape. Move and delete
records use the strict extended shape:

| Kind | `target` meaning | `staged` | `content_hash` | `backup` / `preimage_hash` | `destination` |
| --- | --- | --- | --- | --- | --- |
| legacy replace | destination file | required | required postimage hash | both present for an existing file, otherwise both null | field absent |
| `move` | source file | null | equal to the source preimage hash | both required | required and initially absent |
| `delete` | file to remove | null | null | both required | null |

The extended shape must contain both `kind` and `destination`; an extended record claiming
`kind: replace`, an unknown kind, an incomplete field pair, or any other shape is invalid. This
keeps old version-1 intents readable without weakening validation of newly introduced kinds.

`apply(intent, lock=holder)` reloads the durable intent, verifies all current files and recovery
assets, fences before mutation, and processes operations in recorded order. A replacement moves
its staged postimage over the target; a move atomically replaces its absent destination with the
source; and a delete removes its source while retaining the preimage backup. A destination that
exists while its move source remains pending is a conflict. Operation parents are
directory-fsynced after mutation, including both distinct parents of a move.

Destination refusal is enforced at prepare and immediately before every retry under the
exclusive context-writer lock. As with ADR 0006's documented fence window, Phase 1 does not
promise atomic no-clobber behavior against a non-cooperating process that creates a destination
between the final absence check and `os.replace`.

Every `PermissionError` from replacement, move, or deletion is retried up to ten total attempts
with delays of 0.01 through 2.56 seconds (5.11 seconds total). Before every attempt, the adapter
reverifies the applicable source and destination state and the same lock holder; takeover or
byte drift aborts the retry. On exhaustion, `RecoverableReplaceError` preserves `intent.json`
and every remaining recovery asset. Other operation failures do the same.

`rollback` processes operations in reverse order. Replacements restore their preimages and
recreate a consumed staged postimage when necessary; moves atomically return the destination to
the original source; and deletes restore a copy of the retained backup. These inverse operations
leave the forward assets available, so an interrupted rollback remains resumable and an intent
that has been rolled back can still be completed. `complete_recovery` and `rollback_recovery`
are deliberately separate APIs: they allow a successor lock holder to resolve an old-nonce
intent while ordinary `apply` and `rollback` remain bound to the original intent nonce. The
transaction layer chooses the repair direction and records the durable audit decision; this
adapter only supplies the verified filesystem primitives.

A successful `apply` deliberately leaves `intent.json`. ADRs 0006 and 0010 require the future
transaction layer to fence again, durably append the audit event, and only then call the
matching finalizer. `finalize_after_audit` is original-nonce-bound;
`finalize_recovery_after_audit` finalizes a successor's completed recovery; and
`finalize_rollback_after_audit` verifies every restored preimage and retained postimage before
cleanup; for a move it also requires the destination to be absent. Finalization removes and
directory-fsyncs the intent first, then removes transaction staging. A cleanup interruption
therefore leaves an orphan directory rather than an intent that points to deleted recovery data.

`inspect_recovery` is read-only and reports:

| State | Meaning |
| --- | --- |
| `clean` | No intent; any listed transaction directories are harmless orphans. |
| `invalid_intent` | The intent is malformed, unsafe, or unsupported. |
| `prepared` | Every operation is in its recorded preimage state and all required recovery assets are valid. |
| `partially_applied` | Some operations have reached their postimage state and the rest remain recoverably pending. |
| `fully_replaced_awaiting_audit` | Every operation is applied, but audit/finalization is pending; the legacy state name is retained for compatibility. |
| `recovery_conflict` | A source, destination, staged postimage, or backup differs from every valid state, or an operation parent is unavailable. |

Per-target inspection includes the operation kind, optional move destination and destination
hash, current source hash, staged-postimage hash, and preimage-backup hash. The target-level
state name `staged_postimage_available` is also retained for compatibility and means that any
operation kind is recoverably pending. Inspection does not claim whether byte-identical content
was historically replaced or moved. The transaction engine owns transaction and audit semantics
and chooses whether the adapter completes or rolls back a recoverable intent.

## Atomic line append

`atomic_append_line_bytes(context_root, target, line, nonce=..., lock=...)` provides the durable
write slot required by ADR 0010 without implementing JSON, hash-chain, or ledger-idempotency
semantics. `line` must be non-empty `bytes` containing exactly one final LF and no other LF or
carriage return. An existing non-empty target must already end in LF; an incomplete final line
raises `RecoveryRequiredError` instead of being hidden by another append.

The append is copy-on-write. The adapter snapshots the existing regular file, assembles the
complete old-bytes-plus-line postimage, writes and fsyncs it to a unique same-volume
`98_state/staging/append-<random>.stage`, and verifies its hash. Immediately before every atomic
replacement attempt it rechecks the staged hash, the target preimage hash and path safety, then
verifies the supplied lock nonce as the final fence. `PermissionError` uses the same bounded
retry schedule as staged operations; exhaustion raises `RecoverableReplaceError` and leaves the
canonical target byte-identical. A successful replacement is followed by a best-effort
directory fsync of the target parent. Consequently readers see either the complete previous
file or the complete additional line, never an injected partial-line write.

Following ADR 0006's canonical-write rule means each append copies the current file and is
linear in its size. Phase 1 accepts that cost while ADR 0010 defers rotation until ledger size
becomes a measured problem; the replacement also prevents a context hard link from mutating an
inode reachable outside the context boundary.

Missing target parents are created one component at a time only after an initial fence. Each
component is kept inside a permitted canonical zone, rejects files, symlinks, junctions, and
nested context boundaries, is re-resolved after creation, and has its parent directory fsynced.
The same plain-parent-chain checks run before every retry. Traversal, absolute, drive-relative,
wrong-zone, nested-context, and link-substitution attempts fail closed.

Unlike `atomic_replace_bytes`, the append primitive deliberately permits a structurally valid
active `intent.json`, including an old-nonce intent being handled by a successor lock holder.
It still parses and path-validates any present intent before creating append parents or
publishing bytes; malformed, symlinked, or otherwise unsafe intent state fails closed. The ADR
0010 sequence is therefore:

1. apply, complete, or roll back the staged operations, leaving the intent durable;
2. call `atomic_append_line_bytes` with the current verified lock holder;
3. call the matching original-holder, recovery, or rollback finalizer.

The append neither rewrites the intent nor removes transaction recovery assets. The transaction
engine is responsible for producing the audit line, detecting a previously committed transaction
after a process interruption, and choosing the matching finalizer.

## Owned `98_state` layout

```text
98_state/
├── lock.json                              active writer identity
├── lock.json.tmp                          transient heartbeat postimage
├── lock.stale-<UTC timestamp>[-N].json    preserved takeover evidence
├── staging/
│   ├── intent.json                        one durable active write intent
│   ├── lock.guard.choosing-<nonce>.json   transient mutation-guard number selection
│   ├── lock.guard.ticket-<nonce>.json     transient mutation-guard ownership ticket
│   ├── lock.guard.cancelled-<kind>-<nonce>.json
│   │                                          retired guard blocked from physical removal
│   ├── single-<random>.stage              transient single-file postimage
│   ├── append-<random>.stage              transient complete append postimage
│   └── transactions/
│       └── txn-<random>/
│           ├── 00000000.stage             replacement postimages only
│           ├── 00000000.backup            preimages; mandatory for move and delete
│           ├── 00000000.rollback          transient backup restoration
│           ├── 00000000.rebuild-<random>.tmp
│           │                                  transient postimage reconstruction
│           └── intent.complete.tmp         transient complete intent before publication
└── backups/
    └── context-backup-<UTC timestamp>-v<schema>.tar.gz
```

Timestamps use the Windows-safe `YYYYMMDDTHHMMSS.ffffffZ` form; backup filename collisions add
a numeric suffix. `backups/` is reserved for the migration flow in ADR 0007, which creates the
archive only after acquiring the context lock. The store adapter itself does not create
migration backups.

`98_state/index.sqlite3`, its WAL/SHM siblings, and projection temporary files belong to the
SQLite adapter and are intentionally outside this adapter's ownership.

## User-level registry

`ContextRegistry` stores `contexts.json` beneath the platformdirs user configuration directory,
never inside a workspace. It maps sorted context IDs to canonical machine-absolute roots and
stores an explicit nullable `active_context_id`. APIs list, register, unregister, get, set or
clear active, and resolve the active root. Registration validates that `context.yaml` contains
the same ID, is byte-idempotent for the same mapping, and refuses silent rebinding unless the
caller explicitly requests replacement. The registry file itself is written through a
fsynced temporary file and atomic replacement. Mutations hold an OS-released advisory lock on
the stable, regular `.contexts.json.lock` sentinel across the complete read-modify-write
sequence. Concurrent registrations cannot silently overwrite each other, and a terminated
owner cannot permanently strand the registry. Duplicate JSON object keys and symlinked registry
or guard files fail closed. A caller-supplied registry path remains available for isolated
configuration and tests, but is rejected when it resolves inside any context root.

No active context is inferred merely because one registration exists. A missing, malformed,
unregistered, moved, or ID-mismatched active entry fails closed. CLI context resolution uses
the registry as step 3: the active-context fallback runs only after an explicit path and
ancestor discovery both fail.
