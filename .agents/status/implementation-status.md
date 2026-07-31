# Implementation status

## Baseline

- Baseline commit: `ea6861f956326c35fbbca2bcaf276f423e08570f` (Wave 1 base)
- Plan revision: `.agents/plan/initial/` at `4e2aa2c` (plan state: proposed, adopted by lead)
- Target release: `0.1.0-alpha`
- Last updated: 2026-07-30
- Updated by: implementation lead (Claude Code session)

## Current wave

Wave 3 (opened 2026-07-31): ADR 0012 operator-ratified (D-020 closed). WP-300 and
WP-320 run in parallel now; WP-310 and WP-330 are proposed until WP-300 integrates.
Integration order: WP-320 on acceptance (independent) / WP-300 -> then WP-310 || WP-330
-> lead wiring (inbox/proposal/transaction/agent CLI commands, WP-320 MCP-config
finalization). WP-001 remains partial (CI matrix + [project.urls] await a GitHub
remote).

## Work-package status

| Work package | State | Dependencies | Assigned agent | Branch/worktree | Review | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| WP-000 | closed | — | lead | master / . | operator ratified 2026-07-30 | Status dir, ADRs 0005-0008 (amended after adversarial verify, accepted), 5 work orders, path ownership, [audit record](audit-2026-07-30-scaffold.md) |
| WP-001 | reported | WP-000 | lead | master / . | [report](../work-orders/WP-001-dev-foundation/report.md) | `partial`: gate green, LF policy, lock, build guard all landed in `ea6861f`; CI matrix + [project.urls] blocked on missing GitHub remote |
| WP-100 | verified | WP-000, WP-001 | Codex worker | agent/WP-100-reference-contracts | accepted | Integrated at `5c9cd03`; combined gate green (148 tests) |
| WP-110 | verified | WP-000, WP-001 | Codex worker | agent/WP-110-workspace-schema | accepted | Integrated at `c577801`; combined gate green (222 tests); D-018 cross-check OK |
| WP-120 | verified | WP-000, WP-001 | Codex worker | agent/WP-120-cli-envelope | accepted | Integrated at `788aa1c`; final Wave 1 gate green |
| WP-130 | verified | WP-000, WP-001 | Codex worker | agent/WP-130-skill-contract | accepted | Integrated at `6dfba1c`; final Wave 1 gate green |
| WP-200 | verified | WP-100, WP-110 | Codex worker | agent/WP-200-canonical-store | accepted | Integrated; combined gate 623 tests |
| WP-210 | verified | WP-100, WP-110 | Codex worker | agent/WP-210-sqlite-projections | accepted | Integrated at `0343911` |
| WP-220 | verified | WP-100, WP-110 | Codex worker | agent/WP-220-validation-engine | accepted | Integrated (resequenced before WP-200); FreshnessProbe wiring pending Wave 2 close |
| WP-230 | verified | WP-100, WP-210 | Codex worker | agent/WP-230-context-packs | accepted | Integrated at `d64bcab` after one ADR 0011 revision round; combined gate 707 tests |
| WP-300 | ready | WP-200, WP-210, WP-220 | — | agent/WP-300-transaction-engine | — | Ready at Wave 3 baseline pin |
| WP-310 | proposed | WP-200, WP-220, WP-300 | — | agent/WP-310-inbox-lifecycle | — | Base pinned after WP-300 integrates |
| WP-320 | ready | WP-120, WP-130 | — | agent/WP-320-agent-installers | — | Ready at Wave 3 baseline pin; MCP-config portion deferred post-WP-330 (D-014) |
| WP-330 | proposed | WP-120, WP-220, WP-230, WP-300 | — | agent/WP-330-mcp-server | — | Base pinned after WP-300 integrates; narrow cli/pyproject/ci grants |
| WP-400..WP-500 | proposed | see backlog | — | — | — | Wave 4+ blocked on Wave 3 |

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
