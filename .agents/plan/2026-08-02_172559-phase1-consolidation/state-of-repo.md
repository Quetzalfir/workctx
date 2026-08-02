# State of the repo — Phase 1 consolidation (2026-08-02)

Baseline: master `727efa5` · gate 1301 passed / 6 recorded Windows skips · mypy strict
87 source files · wheel+sdist build green. Waves 0-2 closed; Wave 3 closed except
WP-310 (worker running) and the lead wiring batch. Sources: lead reviews under
`.agents/work-orders/*/leader-review*.md`, integration log, and three read-only
explorer sweeps (2026-08-02) for file:line precision.

## What EXISTS (integrated and verified)

| Module | Content | Anchor |
| --- | --- | --- |
| `domain/` | IDs, URIs (+artifact/repo refs), locators, relations, 19-value vocabulary, observations, claims, entities, tasks(+hierarchy), artifacts, transactions models, frontmatter parser; 44-symbol public API | `src/workctx/domain/__init__.py` |
| `adapters/filesystem/` | ADR 0005 serializer + hand-edit detection, zone-aware store, ADR 0006 lock (nonce/fence/takeover), staged replace/move/delete with intent journal + streaming preimages, fenced append, user registry | `staging.py`, `lock.py`, `store.py`, `registry.py` |
| `adapters/sqlite/` | Projection schema, full rebuild w/ swap, FTS5, typed queries (search:562, tasks:507, get_task:485), `readiness_trigger`:240, `invalidate`:253 | `projection.py` |
| `transactions/` | Proposal validation, dry-run, atomic apply, D-031 commit-point recovery, hash-chained ledger + `authenticate_apply_result` (ledger.py:321), receipts | `engine.py:1704-1729` |
| `validation/` | Typed-document engine, reference integrity, task/claim rules, secret/path checks, FreshnessProbe port, strict mode, D-036 opaque evidence zones (engine.py:866) | `engine.py` |
| `retrieval/` | resolve/related/trace/build_pack, deterministic ranking, budgeted 10-section packs, ADR 0011 fixtures | `builder.py:120` |
| `mcp/` | ADR 0012 17-tool surface (contracts.py:172-301), approval gates, per-tool denial, redaction, stdio runner | `application.py` |
| `adapters/agents/` | detect (detection.py:189), plan_install/install (service.py:1196/2124), status:417, open_context (session.py:31), D-032 three-factor authority, D-033 source sets, D-034 kit bridges | `service.py` |
| `presentation/` + `cli.py` | Envelope, exit codes, resolution shell (steps 1,2,3-registry,4); 12 commands registered (see gap map) | `cli.py:41-47` |
| `resources/` | Canonical context template (50 files in wheel) + agent_kit (skills/registry/bridges, synced) | — |
| Process | 12 ADRs accepted, D-001..D-037, 16 leader reviews, integration log | `.agents/status/` |

## What is FACADE (published surface, hollow behind) — explorer-verified

1. **MCP placeholder tools, published unconditionally in tools/list**:
   `inbox_list` → `application.py:410-412`; `artifact_register` → `:418-420`;
   `draft_save` → `:467-469`; shared emitter `:471-480` (NOT-IMPLEMENTED /
   unavailable-dependency). Per ADR 0012 this is deliberate (stable names), but note:
   `artifact_register` and `draft_save` carry `mutation=True`, so clients see them as
   live write tools that always fail.
2. **MCP-config seam (agents)** — constant `not_implemented` threaded through
   `models.py:34,176-181`, `service.py:197-201` (`_feature_mcp`, sole producer),
   `service.py:1837` (hard-coded manifest literal),
   `manifest.py:223-229` (`Literal["not_implemented"]` — schema admits nothing else).
   Closing it requires: widen the Literal, replace `_feature_mcp` with real detection,
   derive `:1837` from it. ~24 propagation call sites enumerated in the explorer log.
3. **`NullFreshnessProbe`** (`validation/freshness.py:41-52`) — exported public API
   with zero call sites since the SQLite probe landed; dead null-object.
4. **Kit prose validator** (`adapters/agents/sources.py:459-460`) — flags EVERY MCP
   tool mention as "unimplemented" (no allowlist parallel to
   `_is_implemented_command` :397-422); only escape is a literal `(planned)` marker.
   Will bite when kit skills start referencing the live ADR 0012 tools.
5. `validation/engine.py:866-869` comment says evidence paths are "scanned" but the
   code skips them (D-036) — comment/behavior drift, one line.

## What is MISSING (alpha-minimum, doc-04 §90-109)

Engines ready, CLI un-surfaced (wiring batch — 9 commands):
`proposal validate` (engine.py:1704), `transaction dry-run/apply` (:1711/:1715),
`search` (projection.py:562), `task list/show` (:507/:485),
`agent detect/install/status/open` (detection.py:189, service.py:1196+2124/417,
session.py:31).

Blocked on engines:
`inbox add/list` → WP-310 (worker running, branch `agent/WP-310-inbox-lifecycle-r3`);
`brief`, `view rebuild` → WP-400; drafting → WP-420; migration/backup/completion →
WP-500 scope decisions.

## Docs facade/stale claims

See `docs-claims.md` in this directory (explorer report; filed after this document).

## Live risks

- D-037 flake watch (one unreproduced failure, 2026-08-02).
- WP-001 still `partial`: CI matrix + `[project.urls]` await a GitHub remote.
- MCP placeholder mutation=True design note (item 1) — candidate Wave 4 refinement.
