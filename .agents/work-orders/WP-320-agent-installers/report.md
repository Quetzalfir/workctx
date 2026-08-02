# Worker report: WP-320-agent-installers

## Status

completed

## Summary

Implemented the typed Codex, Claude Code, and Gemini CLI adapter APIs, including
detection, manifest-driven installation, drift status, targeted repair, safe
uninstall, recovery, and context-session bootstrap. The Round 2 gaps are resolved
by D-032 through D-034: mutation authority is authenticated by a fixed
user-configuration install record, native-verified skills track complete source
sets, and the packaged kit owns self-contained target-flavored bridges. All
required validation commands pass.

## Base and final commits

- Round 3 base: 2c47821c602536284366f5b3aef7806af6c5124d
- Final commit: 2c47821c602536284366f5b3aef7806af6c5124d
  (working-tree delivery; no commit created)
- Branch: agent/WP-320-agent-installers-r2
- The revision worktree was first fast-forwarded from
  bae004764c48f62239b0e622a1a3a7b40187e5ea with
  git merge --ff-only 2c47821.

## Files changed

- src/workctx/adapters/agents/**
- tests/agents_setup/**
- docs/reference/agent-adapters.md
- docs/reference/skill-adapters.md
- schemas/skill-adapter-manifest.schema.json
- src/workctx/resources/agent_kit/**
- scripts/sync_agent_kit.py
- .agents/work-orders/WP-320-agent-installers/report.md
- .agents/work-orders/WP-320-agent-installers/report.json

## Behavior implemented

- Independent, typed Codex, Claude, and Gemini detection using injected executable
  discovery, project markers, fail-safe version support, and capability reports.
- Repository-first canonical skill loading with deterministic packaged-kit fallback
  and registry-derived side-effect advisories.
- Generated and native-verified manifest entries, typed instruction-bridge and MCP
  components, producer validation, project-local backup records, and legacy-manifest
  compatibility.
- D-032 three-factor overwrite/deletion authority: an adapter-scoped schema-valid
  path, current bytes matching the manifest-recorded hash, and the exact raw manifest
  digest matching the fixed per-project install record in the Work Context user
  configuration directory. Failure of any factor makes the whole plan report-only.
- Trusted-record pending transitions bind the client, manifest path, prior and next
  digests, and ordered operation digest. Authority is rechecked after transaction
  intent persistence and immediately before each apply or recovery mutation.
- D-033 native-verified source sets containing every regular file below a skill
  directory as sorted canonical-path/hash pairs plus a domain-separated aggregate
  digest. Added, removed, renamed, and modified auxiliary resources produce precise
  source-change status; missing required inputs block mutation.
- D-034 packaged, self-contained AGENTS, Claude, and Gemini bridge templates. Kit
  synchronization copies only canonical skills and the registry, while context
  AGENTS content is preserved and client bridges are generated only when absent.
- Content-hash-idempotent install, drift-detecting status, targeted repair,
  manifest-listed-only uninstall, process locking, atomic replacement, and durable
  recovery. Modified or unauthenticated managed targets are never overwritten or
  deleted.
- Typed open_context bootstrap that launches only the selected detected executable
  in the requested context root.
- MCP remains explicitly not_implemented with no server identity, configuration
  target, or credential behavior, as required by D-014.
- Negative security coverage proves that client authentication credentials and
  user-global client auth files are not read, copied, configured, or required.

## Validation executed

| Command | Result | Notes |
| --- | --- | --- |
| git merge --ff-only 2c47821 | pass | Fast-forwarded bae0047 to 2c47821. |
| uv run ruff check . | pass | All checks passed. |
| uv run ruff format --check . | pass | 372 files already formatted. |
| uv run mypy src | pass | Success: no issues found in 79 source files. |
| uv run pytest | pass | 1,181 passed and 6 skipped in 342.83s; 1,187 collected. |
| uv run pytest tests/agents_setup/test_install_records.py tests/agents_setup/test_service_authority.py -q | pass | 29 passed in 18.75s. |
| uv run python scripts/sync_agent_kit.py --check | pass | Agent kit is synchronized. |
| uv run pytest tests/test_plan_contracts.py -q | pass | 4 passed. |
| report.json vs .agents/plan/initial/agent-report.schema.json | pass | Draft 2020-12 validation printed report.json valid. |
| allowed-path audit over git status --porcelain=v1 -uall | pass | All 80 changed files are within the amended contract grants. |
| git diff --check | pass | No whitespace errors. |

The six full-suite skips are expected Windows exclusions for case-colliding names,
POSIX descriptor-race and FIFO tests, and invalid Windows filenames.

## Assumptions and decisions

- The operator's Round 2 and Round 3 worktree, branch, and base instructions
  supersede stale location metadata in contract.json; the allowed and forbidden path
  grants remain unchanged.
- D-026 through D-030 define manifest modes, the packaged agent kit, bridge ownership,
  Gemini layout/version policy, and project-local backups.
- D-032 makes the fixed Work Context user-directory install record the manifest
  authority; a project manifest alone is bookkeeping and cannot authorize mutation.
- D-033 defines complete native source sets and their deterministic aggregate.
- D-034 makes bridge templates kit-authored and target-flavored, and limits canonical
  synchronization to skills plus registry.
- D-014 defers MCP configuration generation until WP-330.

## Contract deviations

None.

## Security and migration considerations

- The only user-configuration artifact accessed is Work Context's own fixed
  agent-adapter install record. Its public API has no path override. No client auth
  file, token, credential, or client-global configuration is accessed.
- Adapter targets and source-set paths are allowlisted, project-relative, and checked
  with no-follow filesystem operations. Traversal, links/reparse points, unsafe
  Windows names, case collisions, and credential-capable names are rejected before
  content access or mutation.
- Existing user bridges and all unauthenticated or modified targets remain untouched.
  Transaction preimages and manifest bookkeeping use retained project-local backups;
  uninstall never recursively removes neighboring user content.
- Tests use isolated fake project/home directories and fake executable discovery; no
  installed client is needed.
- Readers accept legacy generated manifests; current writers emit explicit modes,
  components, and complete native source sets. Successful install/uninstall updates
  the separate trusted install record without migrating or inspecting client-global
  state.

## Unresolved issues

None within WP-320. MCP configuration remains intentionally NOT-IMPLEMENTED pending
WP-330 and is not a completion gap.

## Recommended next action

Lead review and integration, followed by the separately owned CLI wiring. WP-330 may
later implement the reserved MCP seam using its defined server identity.
