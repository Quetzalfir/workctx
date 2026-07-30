# Implementation status

## Baseline

- Baseline commit: `4e2aa2c` (scaffold) — Wave 1 baseline pending WP-001 completion
- Plan revision: `.agents/plan/initial/` at `4e2aa2c` (plan state: proposed, adopted by lead)
- Target release: `0.1.0-alpha`
- Last updated: 2026-07-30
- Updated by: implementation lead (Claude Code session)

## Current wave

Wave 0 (WP-000 lead baseline: deliverables written, pending operator ratification of ADRs
0005-0008 and the baseline commit; WP-001 in execution by the lead).

## Work-package status

| Work package | State | Dependencies | Assigned agent | Branch/worktree | Review | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| WP-000 | reported | — | lead | master / . | operator ratification pending | Status dir, ADRs 0005-0008 (amended after adversarial verify), 5 work orders, path ownership, [audit record](audit-2026-07-30-scaffold.md) |
| WP-001 | in_progress | WP-000 | lead | master / . | — | All changes prepared and verified locally (gate green, uv build OK, wheel contains all 45 template files, LF policy in place); [project.urls] deferred until a remote exists; awaiting operator approval to commit |
| WP-100 | proposed | WP-000, WP-001 | — | agent/WP-100-reference-contracts | — | Ready to assign once baseline commit is pinned |
| WP-110 | proposed | WP-000, WP-001 | — | agent/WP-110-workspace-schema | — | Ready to assign once baseline commit is pinned |
| WP-120 | proposed | WP-001 | — | agent/WP-120-cli-envelope | — | Ready to assign once baseline commit is pinned |
| WP-130 | proposed | WP-000, WP-001 | — | agent/WP-130-skill-contract | — | Ready to assign once baseline commit is pinned |
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

- Operator ratification of ADRs 0005-0008 (proposed; 0005/0006/0007/0008 amended after
  adversarial verification).
- D-019 (audit ledger representation) — must close before WP-300.
- D-020 (first-alpha MCP tool surface) — must close before WP-330 contract creation.
- See `.agents/status/decision-register.md` for the full register (D-001..D-021).

## Active blockers

- Wave 1 assignment blocked until the Wave 0 baseline commit exists (operator approval to
  commit) and ADRs are ratified.
- CI acceptance for WP-001 blocked until the repository has a GitHub remote.

## Integration queue

1. WP-001 (baseline commit on master).
2. Wave 1 integration order after acceptance: WP-100 → WP-110 → WP-130 → WP-120
   (reference vocabulary first so WP-110's entity enum lands against it; CLI envelope last
   because it rewrites tests that other integrations should not race).

## Next lead actions

1. Obtain operator approval for the baseline commit (WP-000 artifacts + WP-001 changes).
2. Execute WP-001, write its report, record the baseline commit hash here and in
   `path-ownership.json`, and flip Wave 1 contracts from `proposed` to `ready` with the
   pinned `base_commit`.
3. Hand the operator the four Wave 1 worktree commands and worker prompts.
4. Review Wave 1 deliveries per the leader review gate.
