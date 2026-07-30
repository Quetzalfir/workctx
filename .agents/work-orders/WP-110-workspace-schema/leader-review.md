# Leader review: `WP-110-workspace-schema`

## Decision

`accepted`

## Contract compliance

- Base commit matches (`ea6861f`); delivery `4169dea`, report `ce06415` on
  `agent/WP-110-workspace-schema`.
- Changed-path audit: 64 files, all inside `allowed_paths`; frozen files untouched
  (no `validation/**`, no `models/__init__.py`, no WP-100 schemas).
- Frozen service signatures verified by diff: `initialize_context`,
  `load_context_config`, `resolve_context_root`, `slugify_context_id` unchanged; only
  private helpers were added/renamed (`_parameterize_template_files`, `_utc_now`,
  `_format_utc_timestamp`).

## Diff review

- `schemas/entity.schema.json` enum equals the D-018 19-value list exactly (verified
  programmatically by the lead against the literal list).
- `schemas/context.schema.json` now requires `created_at`/`updated_at`; the model gains a
  strict `schema_version` validator rejecting unsupported versions with a
  migration-required message — both drift items from the scaffold audit closed, guarded by
  negative fixtures (`context-missing-timestamps`, `context-schema-version`, ...).
- Typed contracts in `domain/entities.py`, `domain/tasks.py`, `domain/artifacts.py`;
  task hierarchy rules (TASK-YYYY-NNN / -STNN grammar, parent/subtask/root consistency)
  enforced with negative tests (`tests/workspace/test_task_hierarchy.py`).
- Template instance validation with resolved `$refs`
  (`tests/workspace/test_template_instances.py`, `schema_support.py` registry) — the
  silent-broken-$ref hazard from the audit is closed.
- Five new frontmatter templates (person, decision, risk, question, claim) added to BOTH
  template trees; `scripts/sync_context_template.py` provides deterministic staged sync
  with the packaged copy declared canonical (D-011 honored); byte-equality still
  test-enforced.
- Template timestamps now parameterized at init (`_parameterize_template_files`),
  closing the frozen-placeholder-timestamp gap.

## Validation executed

| Command | Result | Notes |
| --- | --- | --- |
| report.json vs agent-report.schema.json | pass | validated by lead |
| entity enum vs D-018 literal list | pass | programmatic check, 19/19 |
| frozen-signature diff on services/contexts.py | pass | public API unchanged |
| `uv run ruff check .` | pass | worker worktree, independent run |
| `uv run ruff format --check .` | pass | 161 files |
| `uv run mypy src` | pass | 17 source files, strict |
| `uv run pytest` | pass | 95 passed (baseline was 21) |

## Findings

- transaction-proposal and audit-event schemas received fixtures without semantic
  tightening, per the contracted WP-300 boundary.
- Merge with WP-100 (already on master) is file-disjoint by construction; the D-018
  cross-branch equality check runs at integration (below).

## Required revisions

None.

## Integration notes

- Integrated second per the Wave 1 order. Cross-branch D-018 equality
  (WP-100 `vocabulary.py` vs WP-110 `entity.schema.json`) verified on the merged tree.
