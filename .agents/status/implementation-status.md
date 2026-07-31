# Implementation status

## Baseline

- Baseline commit: `ea6861f956326c35fbbca2bcaf276f423e08570f` (Wave 1 base)
- Plan revision: `.agents/plan/initial/` at `4e2aa2c` (plan state: proposed, adopted by lead)
- Target release: `0.1.0-alpha`
- Last updated: 2026-07-30
- Updated by: implementation lead (Claude Code session)

## Current wave

Wave 2 COMPLETE (2026-07-30): WP-200/210/220/230 accepted and integrated (WP-230 after
one ADR 0011 revision round); lead wiring done (index rebuild, --strict, registry
step 3, SQLite FreshnessProbe, ref show/related/trace, context-pack). Final gate:
707 tests + E2E smoke (init -> entity -> rebuild -> ref show -> ten-section pack).
Next: Wave 3 planning (WP-300 transactions, WP-310 inbox, WP-320 installers, WP-330
MCP) — D-020 (MCP surface ADR) must close before WP-330. WP-001 remains partial (CI
matrix + [project.urls] await a GitHub remote).

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
| WP-300..WP-500 | proposed | see backlog | — | — | — | Wave 3+ blocked on Wave 2 |

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

- D-019 (audit ledger representation) — must close before WP-300.
- D-020 (first-alpha MCP tool surface) — must close before WP-330 contract creation.
- See `.agents/status/decision-register.md` for the full register (D-001..D-021);
  ADRs 0005-0008 were operator-ratified on 2026-07-30.

## Active blockers

- WP-001 final acceptance (CI matrix, [project.urls]) blocked until the repository has a
  GitHub remote.

## Integration queue

Wave 1 integration order after acceptance: WP-100 → WP-110 → WP-130 → WP-120
(reference vocabulary first so WP-110's entity enum lands against it; CLI envelope last
because it rewrites tests that other integrations should not race).

## Next lead actions

1. Plan Wave 3: close D-020 (MCP tool surface ADR), then cut WP-300/WP-310/WP-320/
   WP-330 work orders with a fresh path-ownership matrix.
2. On first GitHub push: verify the CI matrix, add [project.urls], close WP-001.
