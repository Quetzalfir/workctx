# Skill adapter manifests

This document is the normative skill-component manifest contract for the planned WP-320
agent installer. It defines how files derived from canonical skills are inventoried and how
their drift is detected. It does not authorize adapter generation in the current phase: no
`.claude/`, `.gemini/`, `.codex/`, or other client-specific copies may be created until
WP-320 implements this contract.

Canonical skill content remains under `.agents/skills/`. Generated adapters are disposable
project-scoped projections and never become a second source of truth.

This v1 schema intentionally inventories skill-derived files only. Client settings, hooks,
instruction bridges, and MCP configuration are not skill outputs and must not be placed in
`skills[].generated`. WP-320 must track those components through their own versioned ownership
records and compose their status with this component; this manifest is authoritative only for
skill drift, repair, and uninstall.

## Manifest location

WP-320 must write one JSON skill manifest per selected adapter. The installation root is the
directory against which every recorded relative path is resolved.

| Installation root | Manifest path |
| --- | --- |
| A valid context root containing `context.yaml` | `98_state/agent-adapters/<adapter>/skill-manifest.json` |
| A repository-only project root | `.workctx/agent-adapters/<adapter>/skill-manifest.json` |

`<adapter>` is exactly `codex`, `claude`, or `gemini`. A valid context root takes precedence
when a directory is both a context and a repository. The manifest path is derived and is not
stored inside the manifest. The manifest is excluded from its own generated-file inventory,
which avoids a recursive content hash.

Existing bridge files such as `AGENTS.md`, `CLAUDE.md`, and `GEMINI.md` are user-controlled
or canonical inputs. WP-320 must never claim them as generated output.

## Serialization and hashes

- A manifest is UTF-8 JSON, indented by two spaces, with no trailing spaces, LF line
  endings, and one trailing newline. Emit object keys in the order shown by the example,
  sort `skills` by `name`, and sort each `generated` array by `path`.
- `schema_version` is `1`.
- `adapter_version` is the selected adapter renderer/layout version, not the Work Context OS
  release or the installed client version. WP-320 starts each adapter at version `1` and
  increments it whenever the same canonical input can produce materially different bytes or
  target paths.
- `generated_at` is an RFC 3339 UTC timestamp using the `Z` designator. Consumers must enable
  JSON Schema format assertion in addition to the schema's UTC pattern. An idempotent install
  that makes no changes preserves both the existing manifest bytes and this timestamp.
- Every `content_hash` is `sha256:` followed by 64 lowercase hexadecimal characters.
- Hash the exact file bytes, including frontmatter, line endings, encoding bytes, and final
  newline. Do not normalize Unicode, whitespace, or newlines before hashing.

The manifest must validate against
[`skill-adapter-manifest.schema.json`](../../schemas/skill-adapter-manifest.schema.json).

## Manifest fields

```json
{
  "schema_version": 1,
  "adapter": "claude",
  "adapter_version": 1,
  "scope": "project",
  "generated_at": "2026-07-30T20:00:00Z",
  "registry": {
    "path": ".agents/skills/registry.yaml",
    "content_hash": "sha256:0000000000000000000000000000000000000000000000000000000000000000"
  },
  "skills": [
    {
      "name": "bootstrap-session",
      "canonical": {
        "path": ".agents/skills/bootstrap-session/SKILL.md",
        "content_hash": "sha256:1111111111111111111111111111111111111111111111111111111111111111"
      },
      "generated": [
        {
          "path": ".claude/skills/bootstrap-session/SKILL.md",
          "content_hash": "sha256:2222222222222222222222222222222222222222222222222222222222222222"
        }
      ]
    }
  ]
}
```

The registry hash makes a permission-classification change detectable even when canonical
skill prose is unchanged. A generated hash distinguishes a stale canonical source from a
locally modified adapter and enables safe repair or uninstall. One canonical skill may
produce multiple generated files for a client, so `generated` is an array.

## Path and inventory rules

All paths are relative to the installation root, use forward slashes, and must not contain
an empty, `.` or `..` segment. Absolute, drive-qualified, UNC, backslash-containing, and
root-escaping paths are invalid. Generated paths must be under the selected client's
project-local root: `.codex/`, `.claude/`, or `.gemini/`, matching `adapter`. WP-320 chooses
the supported native form after client-version detection and records every exact output path.

Schema validation is necessary but not sufficient. Before reading or changing a target,
WP-320 must also enforce these semantic checks:

1. Skill names are unique within the manifest.
2. Each canonical path is exactly `.agents/skills/<name>/SKILL.md`.
3. Generated paths have unique collision keys. Compute a collision key by applying Unicode NFC
   normalization to the complete forward-slash path and then Unicode full case folding. Reject
   duplicate keys on every platform, including case-sensitive platforms.
4. Every generated path belongs to the adapter named by the manifest.
5. The derived manifest and transaction-state paths, the fixed registry path, canonical paths,
   and generated paths contain no symlink or Windows reparse-point leaf or ancestor below the
   installation root, even if it resolves inside the root. Resolution must also remain inside
   the root.
6. Existing manifest, registry, canonical, generated, lock, intent, staged, and backup file leaves
   are regular files; declared staging-directory components are directories. None use Windows
   reserved device names or trailing dots or spaces.
7. The manifest contains no credentials, authentication material, or secret values.

Rules 5 and 6 are validity boundaries, not approval-capable conflicts. A symlink, reparse
point, non-regular target, reserved name, or path-containment failure makes the installation
state `invalid`; every operation must perform zero writes until the unsafe state is removed
outside WP-320.

Apply rules 5 and 6 before reading bytes. Resolve the installation root once to its physical
directory, walk each existing descendant component with `lstat`, and reject links and reparse
points. On POSIX, walk again from an open root directory descriptor using descriptor-relative
`os.open` calls with `O_NOFOLLOW` (and `O_DIRECTORY` for ancestors). On Windows, walk handles with
`CreateFileW`, `FILE_FLAG_OPEN_REPARSE_POINT`, and `FILE_FLAG_BACKUP_SEMANTICS` for directories.
Open the leaf with `CreateFileW`, reject a reparse leaf, and use `GetFinalPathNameByHandleW` to
verify that the opened object remains below the physical root; then repeat and identity-compare
the ancestor walk. Compare the leaf handle's regular-file type and identity with the final
`lstat`, and only then read from that handle. A missing leaf is considered absent only after its
existing ancestors pass this sequence. Any mismatch is `invalid`; never fall back to an ordinary
path-following open.

Equality between manifest names and the current validated registry is a freshness comparison,
not a manifest-validity rule. A well-formed older manifest remains valid so WP-320 can report
`inventory_changed` and repair it.

Extra files not listed in the manifest are unmanaged. They are warnings only and must never
be overwritten or removed merely because they are below an adapter root.

## Status and staleness algorithm

Status is derived and never persisted in the manifest. WP-320 must evaluate in this order:

1. Safely inspect the fixed lock and staging locations before ordinary freshness checks. An
   unsafe transaction-state path is `invalid`; a live lock held by another writer is `busy`.
   A flushed `intent.json` is `recovery_required` and defers ordinary freshness evaluation until
   the recovery rules below run. More than one intent or an invalid intent is `invalid`. A safely
   validated transaction directory without an intent is an `orphan_staging` warning only and
   does not affect ordinary freshness.
2. Derive the manifest path and apply the no-follow safe-read sequence above before opening it.
   An unsafe or non-regular manifest path is `invalid`. If the safely checked leaf is absent,
   report `not_installed`; existing untracked client files remain unmanaged.
3. From the verified handle, decode a JSON object and read only its integer `schema_version`.
   A decode, object-shape, or version-type failure is `invalid`. If `schema_version` is newer
   than supported, report `unsupported` without interpreting other fields; an older unsupported
   value is `invalid`.
4. Validate the complete current-version schema, adapter-to-location match, path containment,
   and manifest-internal semantic rules above. On failure, report `invalid` and perform no
   mutation.
5. If `adapter_version` is newer than the selected adapter implementation supports, report
   `unsupported` and perform no mutation.
6. Load the current canonical inputs before computing desired inventory:
   - Apply the no-follow safe-read sequence to the registry before opening it. An unsafe or
     non-regular registry is `invalid` with `registry_invalid` and must not be read.
   - If the safely checked registry leaf is missing, add `registry_missing`, mark repair as
     blocked, and skip the registry-hash, inventory, current-source, and expected-target
     comparisons that depend on it. Recorded generated outputs may still be checked for missing
     or modified bytes.
   - If the registry cannot be parsed, fails its schema or semantic completeness checks, or has
     duplicate IDs, report `invalid` with `registry_invalid` and perform no mutation.
   - For every ID in a valid registry, derive its canonical path. An unsafe or non-regular source,
     or one that fails the canonical frontmatter schema or portable-skill lint, makes the state
     `invalid` with `source_invalid`. A missing source adds `source_missing` and blocks repair,
     but is not deletion authority.
7. Collect every applicable drift reason independently:
   - `adapter_version_changed` when the recorded older renderer version is not current;
   - `registry_changed` when a present, valid registry hash differs;
   - `inventory_changed` when valid current registry IDs and manifest skill names differ;
   - `source_changed` for a skill present in both inventories whose canonical hash differs;
   - `target_set_changed` when the current renderer expects different generated paths;
   - `generated_missing` for a recorded target that no longer exists;
   - `generated_modified` when an existing safe regular target hash differs from its recorded
     hash.
8. If any `generated_modified` reason exists, report `conflict`; this takes precedence over
   ordinary staleness. Otherwise, report `stale` when any drift reason exists.
9. Report `current` only when no drift reason exists. Unmanaged-file warnings do not change
   an otherwise current status. `registry_missing` or `source_missing` blocks install and repair
   even if the overall read-only status is `conflict` or `stale`; restore valid canonical input
   before mutation.

`generated_at` is informational and never participates in freshness. A missing canonical
skill is not deletion authority while its ID remains in the validated registry. A skill
removed from a valid current registry is instead an intentional desired-inventory change and
is handled by the repair preflight below.

## Install, repair, and uninstall behavior

The planned WP-320 operations must validate paths as untrusted input and show the complete dry
run before changing files. They must never inspect, copy, or record client authentication
credentials. Preflight all targets before the first mutation; an unapproved conflict makes the
operation perform zero writes. Unsafe or non-regular targets are `invalid`, not conflicts, and
cannot be overridden by approval.

### Lock, precondition, and atomic-replacement protocol

For a context installation, WP-320 must acquire and fence the context write lock at
`98_state/lock.json` using
[ADR 0006](../adr/0006-context-locking-and-atomic-writes.md). For a repository-only installation,
it must use `.workctx/agent-adapters/lock.json` with the same `O_CREAT | O_EXCL` acquisition,
owner metadata, random nonce, heartbeat, stale-lock archival, and nonce-fencing rules. One
repository-only lock serializes all adapters.

With the lock held, preflight must use the no-follow safe-read sequence and record every target's
expected state: absent, or its regular-file identity and exact byte hash. Stage new bytes and
verified preimage backups on the same filesystem under
`98_state/staging/agent-adapters/<transaction-id>/` for a context or
`.workctx/agent-adapters/staging/<transaction-id>/` for a repository-only installation. Before
the first target mutation, flush an `intent.json` in that directory containing the transaction
ID, lock nonce, adapter, ordered operations, target paths, expected preimage hashes or `absent`,
desired postimage hashes or `absent`, and staged and backup paths.

Immediately before the first mutation and again before each target operation, verify the lock
nonce and repeat the no-follow path, type, identity, hash, or absence precondition. Abort before
that operation on any mismatch. A previously absent destination is reserved with
`O_CREAT | O_EXCL`; after its identity is recorded, replace the empty reservation with the staged
file. Replace a file with same-filesystem `os.replace(staged, target)`. Delete a file by
same-filesystem `os.replace(target, backup)`, which retains the preimage for rollback. On Windows,
apply ADR 0006's bounded sharing-violation retry policy. Directory fsync is best-effort on POSIX
and a no-op on Windows.

Standard Python path operations do not provide a portable filesystem compare-and-swap against an
arbitrary non-cooperating process. This protocol deliberately makes the same guarantee as ADR
0006: the lock serializes Work Context writers, just-in-time preconditions bound but do not
eliminate the external race window, and the flushed intent plus verified backups make partial or
interleaved application detectable and recoverable. On a mismatch or operation failure before
manifest commit, stop forward application, attempt only the rollback writes recorded in the
intent, and leave the old manifest unchanged. Keep `intent.json` if rollback is incomplete. If
manifest commit succeeds but cleanup fails, keep the intent so recovery can verify and finalize
the postimage. After all outputs and the manifest verify at their recorded postimages, unlink and
directory-fsync `intent.json` first; its atomic absence is the resolved-transaction marker. Only
then remove staged files, backups, and the empty transaction directory. A crash during this later
cleanup therefore leaves orphan staging, never an intent that refers to a removed backup.

Recovery first acquires the appropriate lock, then validates the intent and every referenced
path with the no-follow rules. Each ordered operation must name one relative target, one of
`create`, `replace`, or `delete`, an expected preimage (`absent` or a content hash), a desired
postimage (`absent` or a content hash), and the applicable staged and backup relative paths.
Target collision keys must be unique, and every hashed preimage must have a verified backup.
If the manifest and every target already match their postimages, cleanup finalizes the committed
transaction. If the manifest still matches its preimage and every target matches either its
preimage or postimage, recovery rolls the whole set back to the preimages. Any target matching
neither state, or a postimage manifest with a non-postimage target, is `recovery_conflict` and
causes zero automatic writes. Retain the intent and backups for explicit recovery. After a
successful rollback verifies every preimage, remove the intent first using the same resolved-state
sequence, then clean up the remaining transaction files. A mutating command holding the lock may
clean orphan staging only after the no-follow checks; it deletes regular files bottom-up and then
empty directories, and stops as `invalid` on any unexpected type or path.

### Install and repair

1. Validate the current registry and canonical inventory, then compute the complete desired
   skill/output set using the current renderer version.
2. Stop on any desired path that exists but is not tracked by the old manifest. A path tracked
   under another skill is also a conflict.
3. A missing desired target may be created under the exclusive-reservation protocol. A tracked
   target may be replaced without extra confirmation only when its current hash still matches
   the recorded generated hash and every just-in-time precondition succeeds.
4. A tracked target with different bytes is `generated_modified`. Back it up, then replace or
   delete it only after explicit approval bound to the exact path, the operation (`replace` or
   `delete`), and the observed current hash. Approval for `replace` also binds the desired
   replacement hash. Re-read and re-hash the target immediately before mutation; a mismatch
   invalidates the approval. Without valid approval for every modified target, the entire
   operation performs zero writes.
5. A tracked output is obsolete only when its skill is absent from the valid current registry
   or the current renderer maps that skill to a different path. Remove it only when its current
   hash matches the manifest and every just-in-time precondition succeeds. A modified obsolete
   output is a conflict under step 4.
6. For a path move, stage the new output before removing the matching old output. Stage all
   bytes, retain recoverable backups, and apply the preflighted set as one rollback-capable
   operation. On failure, restore the old target set and leave the old manifest unchanged.
7. Write or atomically replace the manifest under the same transaction protocol only after every
   output reaches the desired state. A current reinstall performs zero writes and preserves
   `generated_at`.

### Uninstall

1. Preflight every recorded output. A missing output is already removed; a hash-matching safe
   regular file is eligible for guarded deletion; and a hash-modified safe regular file is a
   conflict. A non-regular or unsafe target makes the state `invalid` and the whole operation
   performs zero writes.
2. Without explicit approval for every hash-modified regular-file conflict, perform zero writes.
   Approval to delete a modified file binds the exact path, the `delete` operation, and the
   observed current hash. Re-read and re-hash it immediately before deletion; a mismatch
   invalidates the approval. With valid approval, create a recoverable backup for each exact
   modified file before deleting it.
3. Delete only preflighted recorded files. Never delete unmanaged files or recursively delete
   any directory; leave empty directories in place.
4. Remove the manifest last under the same transaction protocol, only after every tracked output
   is absent. On failure, restore deleted files when possible, keep the manifest, inspect actual
   filesystem state before any retry, and report the exact incomplete state.

These rules make canonical edits, generator upgrades, missing outputs, and user-modified
adapters distinguishable without relying on timestamps or chat history.
