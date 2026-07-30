# Implementation status

## Baseline

- Baseline commit: `ea6861f956326c35fbbca2bcaf276f423e08570f` (Wave 1 base)
- Plan revision: `.agents/plan/initial/` at `4e2aa2c` (plan state: proposed, adopted by lead)
- Target release: `0.1.0-alpha`
- Last updated: 2026-07-30
- Updated by: implementation lead (Claude Code session)

## Current wave

Wave 1 — the four work orders are `ready` at base `ea6861f`; ADRs 0005-0008 ratified by
the operator on 2026-07-30. WP-001 is `partial`: CI-matrix verification and
[project.urls] remain blocked until a GitHub remote exists.

## Work-package status

| Work package | State | Dependencies | Assigned agent | Branch/worktree | Review | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| WP-000 | closed | — | lead | master / . | operator ratified 2026-07-30 | Status dir, ADRs 0005-0008 (amended after adversarial verify, accepted), 5 work orders, path ownership, [audit record](audit-2026-07-30-scaffold.md) |
| WP-001 | reported | WP-000 | lead | master / . | [report](../work-orders/WP-001-dev-foundation/report.md) | `partial`: gate green, LF policy, lock, build guard all landed in `ea6861f`; CI matrix + [project.urls] blocked on missing GitHub remote |
| WP-100 | verified | WP-000, WP-001 | Codex worker | agent/WP-100-reference-contracts | accepted | Integrated at `5c9cd03`; combined gate green (148 tests) |
| WP-110 | in_progress | WP-000, WP-001 | Codex worker | agent/WP-110-workspace-schema | — | Worker active from base `ea6861f`; no report yet |
| WP-120 | accepted | WP-000, WP-001 | Codex worker | agent/WP-120-cli-envelope | accepted | Delivery b4e7752 accepted; integration deferred until after WP-110 and WP-130 per integration order |
| WP-130 | in_progress | WP-000, WP-001 | Codex worker | agent/WP-130-skill-contract | — | Worker active from base `ea6861f`; no report yet |
| WP-200..WP-500 | proposed | see backlog | — | — | — | Wave 2+ blocked on Wave 1 |

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

1. Operator creates the four worktrees from `ea6861f` and pastes each worker prompt.
2. Review Wave 1 deliveries per the leader review gate (contracts `ready` → `assigned`
   as the operator hands them out).
3. On first GitHub push: verify the CI matrix, add [project.urls], close WP-001.
