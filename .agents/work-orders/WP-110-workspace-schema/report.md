# Worker report: `WP-110-workspace-schema`

## Status

`completed`

## Summary

Implemented the WP-110 workspace contracts on `agent/WP-110-workspace-schema`.
Entity, task, artifact-manifest, and context documents now have aligned typed models and
JSON Schema contracts guarded by ADR 0008 fixtures. The canonical workspace template and
all frontmatter templates validate with resolved cross-schema references, task hierarchy
rules are enforced in code, context initialization records real timestamps and the selected
kind/profile without changing its public signature, and the public template mirror is
checked and repaired by a deterministic staged sync.

## Base and final commits

- Base commit: `ea6861f956326c35fbbca2bcaf276f423e08570f`
- Final implementation commit: `4169deab602fcd4b2bf7a4001380c48947e0a783`

## Files changed

- Added typed contracts in `src/workctx/domain/entities.py`,
  `src/workctx/domain/tasks.py`, and `src/workctx/domain/artifacts.py`.
- Aligned `src/workctx/models/context.py`, `src/workctx/services/contexts.py`, and the
  context, entity, task, and artifact-manifest schemas.
- Updated the packaged canonical template under
  `src/workctx/resources/context_template/`, added person, decision, risk, question, and
  claim templates, and synchronized the generated `templates/context/` mirror.
- Added `scripts/sync_context_template.py` and documented canonical direction and profile
  behavior in `docs/guides/context-layout.md`.
- Added contract fixtures and model/schema/template/hierarchy tests under
  `tests/workspace/`, plus focused context and sync coverage in the existing test modules.
- Added this report and `.agents/work-orders/WP-110-workspace-schema/report.json`.

## Behavior implemented

- `EntityFrontmatter`, `Task`, and `ArtifactManifest` enforce schema version 1, RFC 3339
  timezone-aware timestamps, schema-compatible nullable/default behavior, and typed
  vocabularies. `WorkctxUri` is imported only through `workctx.models.reference`.
- Entity references are validated against the frozen reference-schema shape. URI grammar is
  aligned where JSON Schema can express it; URI type/ID equality remains a tested code-only
  invariant.
- The entity vocabulary is the literal D-018 list of exactly 19 values in both model and
  schema tests.
- Task IDs enforce `TASK-YYYY-NNN` / `TASK-YYYY-NNN-STNN`; parent tasks are self-rooted,
  subtasks match an existing parent/root, duplicates are rejected, and a hierarchy cannot
  span context IDs.
- `context.schema.json` requires creation/update timestamps. All context and policy fields
  align with the model, unsupported versions produce a migration-required error, and
  `security_boundary: isolated`, repository language `en`, and
  `federated_search: false` remain enforced.
- Template instance tests validate `context.yaml` and every frontmatter template with a
  Draft 2020-12 registry and an explicit RFC 3339 format checker. A deliberately broken
  cross-schema reference fails rather than passing silently.
- Template synchronization checks byte equality, repairs drift idempotently, rejects
  symlinks, stages before replacement, restores the prior mirror after replacement failure,
  and retains a recovery copy if rollback itself fails.
- Context initialization validates its configuration before mutation, safely rejects
  non-empty targets, parameterizes IDs/timestamps, preserves caller values containing
  placeholder text, and persists selected kind/profile. The four frozen public signatures
  are unchanged.

## Validation executed

| Command | Result | Notes |
| --- | --- | --- |
| `uv run ruff check .` | Passed | `All checks passed!` |
| `uv run ruff format --check .` | Passed | `161 files already formatted` |
| `uv run mypy src` | Passed | `Success: no issues found in 17 source files` |
| `uv run pytest` | Passed | `95 passed in 1.88s` |
| `python scripts\sync_context_template.py --check` | Passed | `Context template mirror is synchronized.` |
| `git diff --check` | Passed | Exit code 0; no whitespace errors. |
| `uv run python -c "import json; from pathlib import Path; from jsonschema import Draft202012Validator; root = Path('.'); schema = json.loads((root / '.agents/plan/initial/agent-report.schema.json').read_text(encoding='utf-8')); report = json.loads((root / '.agents/work-orders/WP-110-workspace-schema/report.json').read_text(encoding='utf-8')); Draft202012Validator(schema).validate(report); print('WP-110 report.json validates against agent-report.schema.json')"` | Passed | `WP-110 report.json validates against agent-report.schema.json` |
| PowerShell allowed/frozen-path audit over `git status --porcelain=v1 --untracked-files=all` and `git diff --exit-code` | Passed | 62 implementation paths, 0 outside `allowed_paths`; frozen-path diff exit code 0. |

The four required validation commands above were executed after the final source, schema,
fixture, and test changes.

## Assumptions and decisions

- All context kinds and profiles retain the required canonical directory zones. The profile
  is persisted as a usage default; the source plans do not define profile-specific directory
  removal or a separate template tree.
- Standard Draft 2020-12 cannot compare the entity URI's embedded type/ID to sibling fields,
  so exact identity equality is enforced and negatively tested in the Pydantic model.
- The packaged `src/workctx/resources/context_template/` tree is authoritative. The public
  `templates/context/` tree is a generated, byte-identical mirror.
- Context/entity/task/artifact models accept in-memory aware `datetime` values as well as
  RFC 3339 strings, while rejecting numeric Unix timestamps. Canonical serialization emits
  strings.

## Contract deviations

- The frozen WP-100 `reference.schema.json` makes `confidence`, `source_observations`, and
  `note` optional but non-nullable. To remain schema-valid, absent values for those nested
  fields are omitted instead of emitted as null, which is a scoped exception to ADR 0005's
  universal null-emission rule. Schema-nullable `valid_from` and `valid_to` are emitted as
  null, and all emitted fields preserve declaration order. WP-110 did not modify the frozen
  schema or stable shim.

No public service signatures, frozen files, or other WP-110 acceptance criteria were
changed outside the contract.

## Security and migration considerations

- All fixtures and templates use fictional values and contain no credentials or private
  employer data.
- Isolated context boundaries, local-only federated search, English canonical repository
  content, typed URI validation, cross-context task rejection, and symlink rejection remain
  explicit and tested.
- Every typed versioned contract accepts only schema version 1. Unsupported versions fail
  with a migration-required message rather than being accepted as a future format.
- Raw evidence zones and the required directory contract were not changed; frozen
  validation code was untouched.

## Unresolved issues

- Lead integration should reconcile WP-100's optional/non-null reference fields with ADR
  0005's universal null policy. This is outside WP-110's allowed paths.
- Transaction-proposal and audit-event semantics were not tightened. WP-110 added positive
  and negative ADR 0008 fixtures only, preserving the explicit WP-300 ownership boundary.
- A low-level copy, parameterization, or write failure after context target creation can
  leave a partial new workspace. WP-110 moved all discoverable input/configuration
  validation before mutation and preserves non-empty targets; fully transactional context
  creation remains future filesystem-workflow hardening.

## Recommended next action

The implementation lead should inspect commit
`4169deab602fcd4b2bf7a4001380c48947e0a783`, rerun the four validation commands, verify
cross-branch D-018/reference compatibility with WP-100, and accept or explicitly track the
reference null-policy integration item.
