# Work-order context: WP-200-canonical-store

## Why this exists

Every mutation workflow needs one safe canonical store. Today `services/contexts.py`
writes context.yaml directly with pinned emitter params, nothing implements the ADR 0006
lock/staging protocol, and there is no path-boundary enforcement or user registry.
WP-300 composes your primitives into transactions; WP-210 consumes your documented
98_state layout.

## Required architecture and decisions

- ADR 0005 (+ ADR 0009 null scope): your serializer IS the reference implementation.
- ADR 0006: lock nonce identity, fencing, intent journal, Windows retry policy — the ADR
  was hardened after adversarial review; implement it exactly, deviations are blockers.
- ADR 0010: the audit ledger lives at 99_meta/audit/ (WP-300 writes it); your staging
  primitives must not claim that path.
- D-013: you own resolution step 3 (registry) as API only; the lead wires presentation.

## Existing implementation

- `workctx.domain.frontmatter` (lead-provided, frozen): parse/split for the read side —
  do not write a second parser.
- Domain models (WP-100/WP-110, integrated): EntityFrontmatter, Task, ArtifactManifest,
  ContextConfig, WorkctxUri etc. — import from `workctx.domain` / `workctx.models.context`.
- `services/contexts.py::_write_context_config` already uses the pinned PyYAML params —
  replace its body with a call into your serializer; public signatures frozen.
- `tests/workspace/` validates template instances; your store reads those same documents.

## Dependencies

- WP-100 and WP-110 are integrated on your base commit. WP-210 and WP-220 run in
  parallel; file-disjoint by contract. 98_state runtime layout: you own `lock.json`,
  `staging/**`, `backups/` naming; WP-210 owns `index.sqlite3*`. Do not touch theirs.

## Known risks and edge cases

- Windows: os.replace PermissionError under concurrent readers is steady-state — the
  bounded retry policy is contractual; CPython readers must use open-read-close.
- Junction/symlink tests need platform guards; skipping requires a recorded reason.
- fsync on directories is a no-op on Windows — best-effort per ADR 0006.
- The heartbeat refresh must preserve the nonce byte-for-byte; a takeover between fence
  checks is detectable only via the intent journal — that interplay needs an explicit
  failure-injection test.
- New test directory `tests/filesystem/` needs an `__init__.py` (pytest basename
  collisions with flat tests).
- Keep store APIs synchronous and dependency-free; async and richer repositories are
  future scope.
