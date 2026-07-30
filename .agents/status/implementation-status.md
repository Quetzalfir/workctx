# Implementation status

## Baseline

- Baseline commit: `ea6861f956326c35fbbca2bcaf276f423e08570f` (Wave 1 base)
- Plan revision: `.agents/plan/initial/` at `4e2aa2c` (plan state: proposed, adopted by lead)
- Target release: `0.1.0-alpha`
- Last updated: 2026-07-30
- Updated by: implementation lead (Claude Code session)

## Current wave

Wave 1 COMPLETE (2026-07-30): all four work orders accepted and integrated in the planned
order (WP-100 → WP-110 → WP-130 → WP-120). Final combined gate: 344 tests, mypy strict on
29 files, build + wheel content check, end-to-end CLI smoke. ADR 0009 resolved the WP-110
null-policy escalation. Next: Wave 2 planning (WP-200, WP-210, WP-220, WP-230) — D-019
(audit ledger ADR) must close before WP-300 contracts are cut. WP-001 remains `partial`:
CI-matrix verification and [project.urls] blocked until a GitHub remote exists.

## Work-package status

| Work package | State | Dependencies | Assigned agent | Branch/worktree | Review | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| WP-000 | closed | — | lead | master / . | operator ratified 2026-07-30 | Status dir, ADRs 0005-0008 (amended after adversarial verify, accepted), 5 work orders, path ownership, [audit record](audit-2026-07-30-scaffold.md) |
| WP-001 | reported | WP-000 | lead | master / . | [report](../work-orders/WP-001-dev-foundation/report.md) | `partial`: gate green, LF policy, lock, build guard all landed in `ea6861f`; CI matrix + [project.urls] blocked on missing GitHub remote |
| WP-100 | verified | WP-000, WP-001 | Codex worker | agent/WP-100-reference-contracts | accepted | Integrated at `5c9cd03`; combined gate green (148 tests) |
| WP-110 | verified | WP-000, WP-001 | Codex worker | agent/WP-110-workspace-schema | accepted | Integrated at `c577801`; combined gate green (222 tests); D-018 cross-check OK |
| WP-120 | verified | WP-000, WP-001 | Codex worker | agent/WP-120-cli-envelope | accepted | Integrated at `788aa1c`; final Wave 1 gate green |
| WP-130 | verified | WP-000, WP-001 | Codex worker | agent/WP-130-skill-contract | accepted | Integrated at `6dfba1c`; final Wave 1 gate green |
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

1. Plan Wave 2: create work orders for WP-200, WP-210, WP-220, WP-230 with a fresh
   path-ownership matrix over the integrated tree.
2. Close D-019 (audit ledger ADR) before WP-300 contracts are cut; D-020 (MCP surface)
   before WP-330.
3. Consolidate re-exports (`models/__init__.py`, `domain/__init__.py`) now that Wave 1
   freezes lifted.
4. On first GitHub push: verify the CI matrix, add [project.urls], close WP-001.
