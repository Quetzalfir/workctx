# Worker report: `WP-130-skill-contract`

## Status

`completed`

## Summary

Implemented the portable skill contract: a schema-backed side-effect registry for all 13
canonical skills, uniform skill bodies with explicit permission boundaries, adversarial
portability lint, and a deterministic skill-adapter manifest contract for WP-320. No adapter
copies or product implementation files were created.

## Base and final commits

- Base: `ea6861f956326c35fbbca2bcaf276f423e08570f`
- Reviewed implementation: `e3779602ba4806d980b8654f1a38afa2ccec83d0`
- The report artifacts are committed after the reviewed implementation snapshot; `final_commit`
  in the machine-readable report identifies that implementation snapshot.

## Files changed

- `.agents/skills/registry.yaml` and `.agents/skills/README.md`
- All 13 canonical `.agents/skills/*/SKILL.md` files
- `schemas/skill-registry.schema.json`
- `schemas/skill-adapter-manifest.schema.json`
- `docs/reference/skill-adapters.md`
- `tests/test_skills.py`
- This report and `report.json`

## Behavior implemented

- Classified the exact 13-skill inventory into the five doc-13 side-effect classes, with an
  affirmative explicit-approval requirement for any future `external_write` entry.
- Gave every skill the same ten-section workflow contract, including inputs, dependencies,
  procedure, side effects, stop conditions, durable outputs, validation, and human response.
- Added proper YAML frontmatter parsing and adversarial lint for cross-platform absolute paths,
  secret-like assignments and token forms, CommonMark links, registry completeness, fixed skill
  inventory, and implemented-versus-planned product references.
- Defined a v1 adapter manifest schema and normative WP-320 behavior for exact-byte hashes,
  deterministic serialization, adapter roots and versions, drift precedence, no-follow reads,
  approval-bound conflict handling, lock/fencing, atomic replacement, crash recovery, repair,
  and uninstall.
- Kept `schemas/skill-frontmatter.schema.json` unchanged and generated no `.claude/`, `.gemini/`,
  or `.codex/` adapter directories.

## Validation executed

| Command | Result | Notes |
| --- | --- | --- |
| `uv run ruff check .` | passed | `All checks passed!` |
| `uv run ruff format --check .` | passed | `143 files already formatted` |
| `uv run mypy src` | passed | `Success: no issues found in 14 source files` |
| `uv run pytest` | passed | `98 passed` |
| `uv run pytest tests/test_skills.py -q` | passed | `78 passed` |

## Assumptions and decisions

- The operator's explicit WP-130 assignment authorized work even though the base-branch copy of
  `contract.json` still contains pre-activation `proposed` and `PENDING` metadata.
- The system-provided Codex worktree was used after attaching it to the required
  `agent/WP-130-skill-contract` branch at the resolved base commit.
- Manifest v1 owns skill-derived files only. Client settings, hooks, instruction bridges, and MCP
  configuration require separate versioned ownership records in WP-320/WP-330.
- Current workflows exclude remote delivery, publication, push, merge, and hosted transitions;
  consequently none of the 13 current skills is classified `external_write`.
- CommonMark link parsing uses `markdown-it-py`, already available through the declared Rich
  dependency, because `pyproject.toml` was outside this work order's writable scope.

## Contract deviations

- The physical Codex-managed worktree location differs from the nominal relative worktree path in
  the assignment; the required branch and exact base commit were used.
- No objective, architecture, public-interface, or allowed-path deviation was made.

## Security and migration considerations

- Evidence and connector responses remain untrusted data and are never executable instructions.
- Lint rejects machine-specific paths, credential-like assignments, known token formats, unsafe
  internal links, and undeclared product operations.
- Adapter paths and manifests are treated as untrusted input. Unsafe links, reparse points,
  non-regular files, containment failures, or invalid registries stop mutation and cannot be
  overridden by approval.
- This is an additive v1 registry/manifest contract. It changes no canonical frontmatter schema,
  creates no adapters, and requires no data migration. WP-320 must implement generation and drift
  handling against these formats.

## Unresolved issues

- The lead-owned base copy of `contract.json` still has pre-activation status/base placeholders;
  reconcile that metadata during integration if the lead workflow requires it.

## Recommended next action

Lead review commit `e3779602ba4806d980b8654f1a38afa2ccec83d0`, re-run the full validation gate,
then integrate WP-130 so WP-320 can consume the registry and manifest contracts.
