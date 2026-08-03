# Implementation status

## Baseline

- Baseline commit: `ea6861f956326c35fbbca2bcaf276f423e08570f` (Wave 1 base)
- Plan revision: `.agents/plan/initial/` at `4e2aa2c` (plan state: proposed, adopted by lead)
- Target release: `0.1.0-alpha`
- Last updated: 2026-07-30
- Updated by: implementation lead (Claude Code session)

## Current wave

Wave 4 CLOSED (2026-08-02): WP-400, WP-410 (1 blocker round, D-041), WP-420, LEAD-W3,
LEAD-W4, DOCS-R1 all integrated. The doc-04 Phase 1 alpha-minimum CLI is COMPLETE and
the ADR 0012 MCP surface is fully live (no placeholders remain). Full-cycle E2E
verified on real APIs: evidence -> observations -> multi-entity transaction ->
archive -> evidence-backed task -> generated views/brief -> outbox draft, with a
5-event verified hash chain. Remaining for Phase 1: WP-500 (end-to-end alpha fixture,
migration tooling, docs polish, packaging/release).

## Work-package status

| Work package | State | Dependencies | Assigned agent | Branch/worktree | Review | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| WP-000 | closed | — | lead | master / . | operator ratified 2026-07-30 | Status dir, ADRs 0005-0008 (amended after adversarial verify, accepted), 5 work orders, path ownership, [audit record](audit-2026-07-30-scaffold.md) |
| WP-001 | closed | WP-000 | lead | master / . | completed 2026-08-02 | GitHub Quetzalfir/workctx (private); full 3-OSx2-Py matrix + build GREEN (run 30774956045 + successor); [project.urls] added; local-first CI policy |
| WP-100 | verified | WP-000, WP-001 | Codex worker | agent/WP-100-reference-contracts | accepted | Integrated at `5c9cd03`; combined gate green (148 tests) |
| WP-110 | verified | WP-000, WP-001 | Codex worker | agent/WP-110-workspace-schema | accepted | Integrated at `c577801`; combined gate green (222 tests); D-018 cross-check OK |
| WP-120 | verified | WP-000, WP-001 | Codex worker | agent/WP-120-cli-envelope | accepted | Integrated at `788aa1c`; final Wave 1 gate green |
| WP-130 | verified | WP-000, WP-001 | Codex worker | agent/WP-130-skill-contract | accepted | Integrated at `6dfba1c`; final Wave 1 gate green |
| WP-200 | verified | WP-100, WP-110 | Codex worker | agent/WP-200-canonical-store | accepted | Integrated; combined gate 623 tests |
| WP-210 | verified | WP-100, WP-110 | Codex worker | agent/WP-210-sqlite-projections | accepted | Integrated at `0343911` |
| WP-220 | verified | WP-100, WP-110 | Codex worker | agent/WP-220-validation-engine | accepted | Integrated (resequenced before WP-200); FreshnessProbe wiring pending Wave 2 close |
| WP-230 | verified | WP-100, WP-210 | Codex worker | agent/WP-230-context-packs | accepted | Integrated at `d64bcab` after one ADR 0011 revision round; combined gate 707 tests |
| WP-201 | verified | WP-200 | Codex worker | agent/WP-201-staging-extensions | accepted | Integrated; combined gate 777 tests; pre-existing tests untouched |
| WP-300 | verified | WP-200, WP-201, WP-210, WP-220 | Codex worker | agent/WP-300-transaction-engine | accepted (2 blocker rounds) | Integrated; combined gate 938 tests; D-031 commit-point recovery |
| WP-310 | verified | WP-200, WP-220, WP-300 | Codex worker | agent/WP-310-inbox-lifecycle-r3 | accepted (2 blocker rounds) | Integrated; D-035/D-036 architecture; receipt-gated archive |
| WP-320 | verified | WP-120, WP-130 | Codex worker | agent/WP-320-agent-installers-r2 | accepted (3 rounds) | Integrated; D-032 three-factor authority, packaged kit, kit bridges |
| WP-330 | verified | WP-120, WP-220, WP-230, WP-300 | Codex worker | agent/WP-330-mcp-server | accepted | Integrated; ADR 0012 17-tool surface; combined gate 1298 tests |
| WP-400 | verified | WP-110, WP-210, WP-300 | Codex worker | agent/WP-400-tasks-views | accepted | Integrated; evidence-required task mutations |
| WP-410 | verified | WP-230, WP-300, WP-310, WP-330 | Codex worker | agent/WP-410-evidence-workflow | accepted (D-041 round) | Integrated; ingestion MCP tools live |
| WP-420 | verified | WP-230, WP-300, WP-400 | Codex worker | agent/WP-420-drafting-outbox | accepted | Integrated; draft_save live |
| WP-510 | verified | all Phase 1 | Codex worker | agent/WP-510-migration | accepted | Integrated at 684ef8d; D-042 single_import ledger policy |
| WP-520 | verified | all Phase 1 | Codex worker | agent/WP-520-acceptance | accepted | Integrated at 4ca2cba; found the 04_views rebuild defect (lead-fixed 62282bf) |
| WP-530 | in-progress | WP-510 | Claude agent | (claude worktree) | — | Release docs + packaging verification |

## Validation status

| Gate | Last command | Result | Commit | Date |
| --- | --- | --- | --- | --- |
| lint | `uv run ruff check .` | pass (after lead fixes) | working tree on 4e2aa2c | 2026-07-30 |
| format | `uv run ruff format --check .` | pass, 140 files | working tree on 4e2aa2c | 2026-07-30 |
| typing | `uv run mypy src` | pass, 14 source files | working tree on 4e2aa2c | 2026-07-30 |
| tests | `uv run pytest` | 21 passed | working tree on 4e2aa2c | 2026-07-30 |
| contracts | jsonschema validation of all 5 work-order contracts | pass | working tree on 4e2aa2c | 2026-07-30 |
| build | `uv build` + wheel template-content check | pass (45/45 template files present) | working tree on 4e2aa2c | 2026-07-30 |
| CI (3 OS) | GitHub Actions | not yet run (repo not pushed) | — | — |

## Open decisions

- None blocking. All twelve ADRs are accepted (0005-0010, 0012 operator-ratified;
  0009/0011 lead refinements). Full register: D-001..D-023.

## Active blockers

- WP-001 final acceptance (CI matrix, [project.urls]) blocked until the repository has a
  GitHub remote.

## Integration queue

Wave 3: WP-320 integrates on acceptance (independent); WP-300 next; then WP-310 and
WP-330 in either order after review; lead wiring closes the wave.

## Next lead actions

1. Pin the Wave 3 baseline into WP-300/WP-320 contracts and hand the operator their
   worktree commands and prompts.
2. Review deliveries per the leader gate; on WP-300 integration, pin and release
   WP-310/WP-330.
3. At wave close: wire inbox/artifact/proposal/transaction/agent CLI commands,
   finalize WP-320's MCP config against WP-330's server identity, combined gate.
4. On first GitHub push: verify the CI matrix, add [project.urls], close WP-001.
