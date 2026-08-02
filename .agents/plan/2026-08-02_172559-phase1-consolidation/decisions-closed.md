# Closed decisions — do not re-ask

Consolidated 2026-08-02 from the full lead thread. Authority: ADRs under `docs/adr/`
(all twelve accepted) and the register `.agents/status/decision-register.md`
(D-001..D-037). This file is the operator-facing extract.

## Architecture (ADR-backed, operator-ratified)

| # | Decision | Where |
| --- | --- | --- |
| 1 | Canonical Markdown/YAML; SQLite/FTS rebuildable projections only | ADR 0001, 0007 |
| 2 | PyYAML canonical serialization: pinned emitter params, model field order, LF; null-vs-omit driven by schema nullability | ADR 0005 + 0009 |
| 3 | Locking: nonce-identity lock file, atomic heartbeat, stale takeover with archival, fencing; staging with write-ahead intent journal; bounded PermissionError retries | ADR 0006 |
| 4 | Migrations: forward-only in-code steps, single workspace schema_version, backup-then-migrate under lock; projections rebuild, never migrate | ADR 0007 |
| 5 | JSON Schemas hand-maintained (never generated), aligned via positive AND negative fixtures; Draft 2020-12 only; inexpressible relations are producer invariants declared in schema descriptions | ADR 0008 + 0011 |
| 6 | Audit ledger: canonical `99_meta/audit/ledger.jsonl`, prev/event hash chain, Git backstop; THE ledger event is the commit point — recovery is cleanup-only with a verified event, preimage-rollback-only without one | ADR 0010 + D-031 |
| 7 | MCP alpha surface frozen: 11 read + 6 approval-gated mutation tools, stdio only, no prompts, NOT-IMPLEMENTED placeholders keep tool names stable | ADR 0012 |
| 8 | Entity-type vocabulary: fixed 19-value list | D-018 |

## Engineering policy

| # | Decision | Where |
| --- | --- | --- |
| 9 | Live status lives in `.agents/status/`; `.agents/plan/initial/` is immutable history; machine JSON graphs are authoritative for scheduling | D-005, D-014 |
| 10 | Exit codes: doctor required-fail=5, user-correctable=1, usage=2 (Click), boundary=3, conflict=4, stale-derived=6, unexpected=10 | D-015 |
| 11 | Evidence zones (00_inbox/raw, 00_inbox/quarantine, 01_processed) are opaque to validator content checks; bounded scanning happens at ingestion | D-036 |
| 12 | Evidence binaries never enter transaction proposals; physical moves are staged primitives gated by `authenticate_apply_result` | D-035 + D-036 |
| 13 | Installer trust: three-factor mutation authority (scoped path + content hash + user-dir trusted install record); any failure → report-only | D-032 |
| 14 | Packaged agent kit (`resources/agent_kit/`) with deterministic sync; kit-authored target bridges; native-verified source sets for Codex | D-026, D-027, D-033, D-034 |
| 15 | `uv.lock` committed; `.gitattributes` LF policy; CI 3-OS matrix with `--extra mcp` | WP-001 + WP-330 |

## Process

| # | Decision | Where |
| --- | --- | --- |
| 16 | Worker protocol: bounded contracts, blockers are valid results, lead re-runs all gates independently, delivery-capture commits by the lead when a sandbox cannot commit | Thread precedent (WP-320-r3) |
| 17 | Flake watch: one unreproduced suite failure on 2026-08-02; recurrence blocks Wave 4 until root-caused | D-037 |
