# ADR 0006: Context locking and atomic canonical writes

- Status: accepted
- Date: 2026-07-30

## Context

The transaction model requires one canonical writer per context, multiple readers, stale-lock
recovery, and no partially visible canonical mutation, on Windows, macOS, and Linux. The
architecture plan lists the lock implementation and the Windows filesystem atomicity strategy
as decisions to confirm early. An adversarial review of the first draft of this ADR found
three soundness gaps (no fencing after stale takeover, interruption detection that depended
on an audit event written after the point of interruption, and unspecified Windows
sharing-violation behavior); this revision addresses them.

## Decision

### Lock identity

- The context write lock is the file `98_state/lock.json` inside the context root.
- Acquisition creates the file with `O_CREAT | O_EXCL` semantics (`open(..., "x")`), atomic
  on NTFS, APFS, and ext4, writing owner metadata in the same operation: `pid`, `hostname`,
  `session_id`, `tool_version`, `acquired_at` (UTC), `heartbeat_at`, and a random 128-bit
  `nonce`.
- **Holding the lock means the current `lock.json` contains your nonce** — not merely having
  created the file. This distinction makes stale takeover safe (see fencing below).

### Heartbeat

- Long operations refresh `heartbeat_at` by writing `98_state/lock.json.tmp` and atomically
  replacing `lock.json` (`os.replace`), preserving the `nonce` and all identity fields.
  Readers therefore never observe torn JSON.

### Stale-lock recovery

- A lock is stale when its `pid` no longer exists on the same host, or its `heartbeat_at`
  is older than a configurable threshold (default 10 minutes).
- A `lock.json` that cannot be parsed (crash window between create and metadata write is
  eliminated by the single-operation create above, but corruption remains possible) is
  treated as stale when its file mtime is older than the same threshold.
- Recovery renames the stale lock to `98_state/lock.stale-<timestamp>.json` before retrying
  acquisition, so takeover is observable and never silently deletes evidence of the previous
  holder. Recovery must not delete `98_state/staging/` contents (see repair below).
- Stale-lock archives older than 30 days may be pruned by `workctx context validate --repair`.

### Fencing

- Immediately before the commit point (the first `os.replace` of a transaction, and again
  before appending the audit event), the writer re-reads `lock.json` and verifies its own
  nonce. On mismatch or absence the writer aborts: a takeover happened while it slept.
- The interval between fence checks bounds, but does not eliminate, the race window of a
  host that sleeps mid-`os.replace`-sequence; the intent journal below makes any such
  interleaving detectable and repairable. WP-300 acceptance requires failure-injection
  tests for exactly this scenario.

### Atomic writes and interruption detection

- Every canonical write goes to a temporary file in `98_state/staging/` (same volume as the
  context, which `98_state/` guarantees), is flushed and fsynced, then moved into place
  with `os.replace` — atomic on POSIX and on Windows (NTFS) for same-volume moves.
- Multi-file transactions are journaled: after staging all files and validating the staged
  set, the writer fsyncs a **write-ahead intent record** `98_state/staging/intent.json`
  (transaction ID, nonce, ordered target list, content hashes) **before the first replace**,
  and removes it only after the audit event is appended. Recovery/validation finding an
  intent record knows exactly which targets may be partially applied and can complete or
  roll back using the staged files and hashes. The post-hoc audit event alone cannot serve
  this purpose — it does not exist yet at the moment of interruption.
- Directory fsync after replace is best-effort on POSIX and a no-op on Windows.

### Windows sharing violations

- On Windows, `os.replace` fails with `PermissionError` (sharing violation) whenever any
  process holds the destination open without `FILE_SHARE_DELETE` — including concurrent
  CPython readers, antivirus scanners, indexers, and sync clients. This is an expected
  steady-state event, not an edge case, because the architecture allows concurrent readers.
- Policy: bounded retry with exponential backoff (default 10 attempts over ~5 seconds) per
  replace; on exhaustion, abort the transaction, leave the intent record in place, and
  report a recoverable conflict (exit code 4 semantics at the CLI boundary). The intent
  journal makes an abort mid-sequence repairable.
- Core readers must read via open-read-close (no long-lived open handles on canonical
  files) to minimize self-inflicted sharing violations.

### Rejected alternatives

- Byte-range locks (`fcntl` / `msvcrt`): semantics differ across platforms and network
  filesystems, and lock files are inspectable by humans.
- Lock servers or SQLite-based locking: SQLite is a rebuildable projection and must not
  become a correctness dependency for canonical writes (ADR 0001).

## Consequences

- no third-party locking dependency; the protocol is testable with plain files, including
  takeover, fencing-abort, torn-heartbeat, unparseable-lock, and sharing-violation retries;
- the intent journal adds one fsynced write per multi-file transaction — an accepted cost
  for deterministic recovery;
- network filesystems (SMB, NFS) get no additional guarantees in Phase 1; documented
  limitation;
- the audit ledger's representation (open decision D-019) must record transaction IDs that
  the intent journal references; that ADR must land before WP-300 implementation starts.
