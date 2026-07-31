# Worker report: `WP-220-validation-engine`

## Status

`completed`

## Summary

Rebuilt `src/workctx/validation/` as the doc-03 integrity engine while preserving the
consumed validation API. The engine now validates canonical documents through the
integrated domain models, builds in-memory identity and relation indexes, enforces ordered
reference checks and context isolation, diagnoses task and claim graph violations, reports
projection freshness through an adapter-free protocol, and retains hardened structural,
path, encoding, and secret checks. Diagnostics are stable, actionable, documented, and
covered by negative and adversarial regression fixtures.

## Base and final commits

- Base commit: `cf9ebabf10d565bb0eef0d8f686ebada3cdd34ab`
- Final implementation commit: `eedb5734c1c525308d969243e72174299923c34a`

## Files changed

- Replaced the validation facade internals and added the engine, report models,
  diagnostic catalog, and freshness protocol under `src/workctx/validation/`.
- Added `docs/reference/validation-diagnostics.md` with every emitted code, severity,
  cause, repair action, and semantic convention.
- Added isolated validation fixtures and rule regressions under `tests/validation_engine/`.
- Added this report and `.agents/work-orders/WP-220-validation-engine/report.json`.

## Behavior implemented

- Preserved `validate_workspace(root) -> ValidationReport`, `.ok/.errors/.warnings`, the
  original issue fields, and imports used by the CLI/presentation layer. Added keyword-only
  API strictness, optional freshness probing, advisories, and reported repair actions.
- Validates `ContextConfig`, `EntityFrontmatter`, `Task`, `ArtifactManifest`, `Observation`,
  and `Claim` at runtime with Pydantic and `workctx.domain.frontmatter`; no runtime
  `jsonschema`, SQLite, filesystem-adapter, or projection-adapter import was introduced.
- Discovers Markdown canonical documents by frontmatter or stable-ID filename and treats
  every YAML/JSON file in canonical zones as typed. Manifest location and explicit special
  `entity_type` values force the correct special model, preventing generic-model bypasses.
- Builds deterministic in-memory identity, artifact-digest, task, claim, and outbound-edge
  indexes. Reference validation follows parse/canonicalization, context boundary, D-018
  vocabulary, and resolution order; external and unavailable artifact references remain
  advisories.
- Validates the complete task corpus with `validate_task_hierarchy`, maps violations to the
  responsible file, normalizes `blocks`/`depends_on` precedence, and detects relation cycles
  with an iterative linear graph algorithm.
- Enforces half-open claim intervals, detects overlapping current values per exact
  subject/predicate in `O(n log n)`, checks supersession targets, and detects normalized
  supersession cycles without recursion.
- Adds `FreshnessProbe`, `NullFreshnessProbe`, `FreshnessResult`, `FreshnessState`, and
  canonical typed edges. Probe failures and stale/unknown/backlink states are sanitized and
  never affect canonical files.
- Preserves required-directory, UTF-8, absolute-path, secret, and federated-search rules;
  adds exact-case layout checks, unreadable-path diagnostics, symlink/junction refusal,
  portable artifact/repository path checks, secret-safe messages, and bounded linear secret
  matching across common text/config/source formats.
- Validation remains read-only. Repair actions are attached to issues for callers and are
  never executed.

## Validation executed

| Command | Result | Notes |
| --- | --- | --- |
| `uv run ruff check .` | Passed | `All checks passed!` |
| `uv run ruff format --check .` | Passed | `221 files already formatted` |
| `uv run mypy src` | Passed | `Success: no issues found in 35 source files` |
| `uv run pytest` | Passed | `449 passed in 24.45s` |
| `git diff --check` | Passed | Exit code 0; no whitespace errors. |
| PowerShell base-to-worktree allowed-path audit | Passed | 14 changed paths from the assigned base through reports, 0 outside `allowed_paths`. |
| `git diff --exit-code -- <forbidden paths>` | Passed | Exit code 0; no forbidden tracked path changed. |
| `uv run python -c <agent-report schema validation>` | Passed | `report.json` validates against `agent-report.schema.json`. |

The four required validation commands were executed after the final source, documentation,
fixture, and test changes. Independent implementation and adversarial reviewers reported no
remaining reproducible P1/P2 findings; the adversarial focused suite passed 100 tests.

## Assumptions and decisions

- Phase 1 treats each exact claim `subject` plus `predicate` slot as single-valued; arrays
  carry intentional multi-values. Validity intervals are half-open `[valid_from, valid_to)`
  and missing bounds are open-ended.
- Task precedence is normalized as prerequisite to dependent: `A depends_on B`,
  `A.dependencies: [B]`, and `A.blockers: [B]` produce `B -> A`; `A blocks B` produces
  `A -> B`.
- `new.supersedes: old` and `old.superseded_by: new` normalize to one `new -> old` edge.
- Existing frozen CLI behavior permits auxiliary plain Markdown inside canonical zones.
  Markdown is therefore typed when it has frontmatter, while a stable-ID filename without
  frontmatter is a parse error; all canonical-zone YAML/JSON files are typed candidates.
- Omitting a freshness probe is quiet so a freshly initialized canonical-only context has
  no issues. Explicit `NullFreshnessProbe` use reports the documented unknown advisory.

## Contract deviations

None. No CLI, domain model, adapter, schema, template, plan, status, or other forbidden path
was modified.

## Security and migration considerations

- The engine treats content as data, never executes it, never follows workspace
  symlinks/junctions, never reads excluded projection state, and never mutates the validated
  workspace.
- Secret findings expose only a relative location and stable remediation text, never the
  matched value or exception detail. All tests use fictional data.
- Context boundaries are checked before vocabulary and resolution, including invalid model
  documents, so foreign references cannot leak local-index information.
- No persistent format or database migration is introduced. New diagnostics and the
  optional `repair_action` field are additive; the original report and issue surface remains
  source-compatible.

## Unresolved issues

- The SQLite-backed `FreshnessProbe` remains an integration-lead responsibility when
  WP-210 is merged, as required by the work order. This branch intentionally ships only the
  protocol and null implementation.

## Recommended next action

The implementation lead should inspect commit
`eedb5734c1c525308d969243e72174299923c34a`, rerun the four validation commands, connect the
WP-210 projection probe behind `FreshnessProbe`, and accept the work order if integration
behavior remains green.
