# First-wave brief

## Scaffold assessment

Verified by direct execution and a six-agent audit on 2026-07-30 (working tree on
`4e2aa2c`); the preserved audit record is
[audit-2026-07-30-scaffold.md](audit-2026-07-30-scaffold.md):

- The validation gate passes locally after lead fixes (lint B008/I001/RUF100, mypy typed
  construction in `services/contexts.py`, formatting at line-length 100). The shipped
  scaffold had never run the uv-based gates (recorded in `docs/development/scaffold-validation.md`).
- Implemented today: `WorkctxUri` (canonical URI only), context init/inspect/validate with
  ad-hoc JSON shapes, doctor, structural workspace validation, 13 skills with minimal
  frontmatter, 12 JSON Schemas (meta-validated only — $refs never resolved), dual
  byte-identical context templates, 3-OS CI (never executed), 21 passing tests.
- Not implemented: every other doc-04 command family, all domain ID/locator/relation
  contracts, SQLite, transactions, retrieval, MCP, agent installers.
- Known drift found: context.schema.json misses created_at/updated_at in required;
  entity_type enum lacks observation/artifact; concepts.md says "task signal" vs schema
  enum "task"; top-level `validate` alias undocumented in doc-04; quickstart uses an
  `agent install --context` flag doc-04 does not define; prose/JSON dependency mismatches
  for WP-220/WP-300/WP-320.
- Environment risks: no `.gitattributes` with `core.autocrlf=true` machines means a fresh
  Windows checkout fails `ruff format --check` repository-wide; hatchling VCS-ignore
  handling may drop packaged template files from wheels; `uv.lock` absent.

## Decisions required before delegation

- Operator ratification of ADRs 0005 (serialization), 0006 (locking/atomic writes),
  0007 (migrations), 0008 (schema ownership) — all proposed, all four amended after
  adversarial verification (see the audit record).
- Operator approval to create the Wave 0 baseline commit (and push once a remote exists).
- Register decisions D-005..D-018 and D-021 are lead-level and recorded; objections welcome.
- Still OPEN (do not block Wave 1, must close before Wave 3): D-019 audit ledger
  representation (blocks WP-300) and D-020 first-alpha MCP tool surface (blocks WP-330).

## Proposed execution order

```text
Wave 0 (sequential, lead): WP-000 (done, pending ratification) -> WP-001 (baseline commit)
Wave 1 (parallel, 4 workers): WP-100, WP-110, WP-120, WP-130
Integration order: WP-100 -> WP-110 -> WP-130 -> WP-120
```

## Parallel assignments

WP-100, WP-110, WP-120, WP-130 — writable paths verified disjoint at file granularity,
including each order's own report files (`path-ownership.json` is the authority; its
frozen_paths and frozen_interfaces lists are the canonical freeze declaration). Frozen
paths: `models/__init__.py`, `domain/__init__.py` (lead pre-created),
`src/workctx/validation/**`, `schemas/skill-frontmatter.schema.json`, `.agents/plan/**`,
`.agents/templates/**`, `AGENTS.md`, `docs/adr/**`, `pyproject.toml`. Frozen interfaces:
`WorkctxUri` (parse/str/require_context), the four public `services/contexts.py` functions,
and `workctx.errors` existing classes (additive only, WP-120). Schema files split:
reference-side (WP-100) vs workspace-side (WP-110). Entity-type vocabulary: both orders
anchor to the fixed 19-value list in decision D-018 — never to each other's in-flight
branches; cross-branch equality is lead-verified at integration.

## Sequential assignments

- WP-000 and WP-001: lead, on master — WP-001 produces the baseline commit that Wave 1
  worktrees branch from, so nothing may run beside it.
- WP-230 within Wave 2 remains sequential after WP-210 per the backlog.

## Work orders to create

Created under `.agents/work-orders/`:

- `WP-001-dev-foundation` (assigned, lead)
- `WP-100-reference-contracts` (proposed)
- `WP-110-workspace-schema` (proposed)
- `WP-120-cli-envelope` (proposed)
- `WP-130-skill-contract` (proposed)

`proposed -> ready` happens when the baseline commit hash is pinned into each contract's
`base_commit`.

## Path ownership and worktrees

See `.agents/status/path-ownership.json`. Worktree convention:

```text
git worktree add .worktrees/<WO-ID> -b agent/<WO-ID> <BASELINE_COMMIT>
```

## Validation gates

Every work order: `uv run ruff check .`, `uv run ruff format --check .`,
`uv run mypy src`, `uv run pytest` (WP-001 adds `uv build` and `git ls-files --eol`).
Lead reruns all gates independently on every delivery plus combined regression after each
integration. CI (3 OS x Python 3.12/3.13) becomes a gate as soon as the repo is pushed.

## Integration and rollback plan

- Integrate only accepted deliveries, in the order above, one at a time, running the full
  gate after each merge; update the work-package status table in
  `implementation-status.md`, the integration log, and this brief. Per D-005 the plan
  files (including backlog `status` fields) stay immutable — live status lives here.
- Worktree branches are retained until their work order is `verified`; rollback is
  reverting the merge commit on master (no worker commits directly to master).
- All freezes (paths and interfaces) lift at Wave 1 close, when the lead consolidates
  re-exports (`models/__init__.py`, `domain/__init__.py`) in an integration commit.

## Risks and assumptions

- R-004 (incompatible parallel architecture) mitigated by frozen interfaces and file-level
  ownership; residual risk: WP-100/WP-110 vocabulary coordination — lead arbitrates.
- R-006 (Windows atomicity) addressed early via ADR 0006; failure-injection tests land
  with WP-300.
- WP-120's envelope intentionally breaks current `--json` consumers and tests — accepted,
  pre-alpha, no external consumers exist.
- Assumption: single human operator, manual agent sessions per the delegation model; no
  GitHub remote yet — CI acceptance for WP-001 completes after first push.
- Wave labels: machine files (`initial-backlog.json`, `dependency-graph.json`) are
  authoritative for scheduling (D-014); prose drift is recorded, not silently edited.
