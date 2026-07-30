# Scaffold audit — 2026-07-30

Six independent read-only audit agents inspected the working tree at commit `4e2aa2c`
(plus the lead's local gate fixes) before Wave 1 delegation. This document preserves the
load-bearing findings; it is the evidence artifact referenced by the decision register and
the first-wave brief. A four-agent adversarial verification pass then reviewed the lead's
authored artifacts; its accepted findings are folded into the contracts, ADRs, and status
documents (see the integration notes at the end).

## WP-001 — Development foundation

- Complete tooling config exists (pyproject, 3-OS CI matrix, Makefile, pre-commit,
  editorconfig) and all four gate commands agree across CI/Makefile/CONTRIBUTING/testing docs.
- Gaps: no `uv.lock` committed (CI re-resolves every run); no `.gitattributes` — with
  `core.autocrlf=true` (GitHub windows-latest and the operator machine) a fresh Windows
  checkout materializes CRLF and `ruff format --check` (line-ending=lf) fails repo-wide;
  no `uv build` step or wheel-content check; no `[project.urls]`; legacy license metadata;
  pre-commit config exists but the tool is not in the dev group and is undocumented;
  the `mcp` extra is never installed in CI; no coverage threshold; no CI concurrency group.
- Wheel risk: hatchling respects VCS ignore files; the packaged template ships its own
  `.gitignore` and the root ignores `**/98_state/*` — built wheels may silently drop
  template files. `tests/test_template_sync.py` checks in-repo equality only.
- The uv-based gates had never been executed before this session
  (`docs/development/scaffold-validation.md` records them as deferred).

## WP-100 — Reference contracts

- Implemented: `WorkctxUri` (parse/str round-trip, %23 fragment encoding, traversal
  rejection, context boundary); 4 tests. Reference-side schemas encode doc-03 faithfully
  (22-relation vocabulary; 9 locator types; observation/claim ID grammars).
- Missing: every doc-03 ID family in Python; artifact:// and repo:// parsing; locator and
  relation typed models; observation/claim typed models; all doc-03 validation rules that
  JSON Schema cannot express (range ordering, absolute-path rejection, acyclicity checks).
- Coupling: only `models/__init__.py` and `tests/test_reference.py` import
  `models/reference.py` — the move-to-domain refactor is low-risk behind a shim.
- Vocabulary drift: entity.schema.json enum (17 values) lacks observation/artifact used by
  doc-03 URIs → resolved by D-018 (fixed 19-value list).

## WP-110 — Workspace schema and template

- Implemented: ContextConfig + enums with pinned invariants; context lifecycle service;
  full packaged template tree; byte-identical public mirror (test-enforced); schemas for
  all owned documents; structural workspace validation.
- Missing: typed models beyond context; instance validation of the template against its
  schemas; frontmatter templates for 15 of 17 entity types; task-hierarchy semantics;
  claim template; template parameterization beyond context-id string replacement.
- Drift found: context.schema.json does not require created_at/updated_at while the model
  does; schema_version is `const: 1` in schema vs `ge=1` in the model.
- $refs between schemas are never dereferenced in tests — a broken $ref passes CI.
- Shared-file hazards identified (services/contexts.py, models/__init__.py,
  validation/workspace.py straddling WP-220) → resolved via freezes D-006/D-007 and
  file-level ownership D-010.

## WP-120 — CLI framework

- Implemented: Typer app; version/doctor/validate + context sub-app; partial ad-hoc JSON
  shapes per command; typed error hierarchy; exit codes 0/1 only.
- Missing vs doc-04: shared envelope ({ok, command, context_id, result, warnings, errors,
  meta}); stdout purity in JSON mode (Rich error markup currently goes to stdout); exit
  codes 2-6/10+; resolution step 3 and --context; timing; schema_version constant;
  presentation boundary.
- Ambiguities settled by the lead (D-012 alias stays, D-013 registry deferred to WP-200,
  D-015 exit-code mapping) so workers do not improvise mappings.
- Click CliRunner merges stderr into stdout by default — purity tests must split streams.

## WP-130 — Skills and adapters

- All 13 planned skills exist and pass the minimal frontmatter test; bridges (AGENTS.md,
  CLAUDE.md, GEMINI.md, copilot-instructions) exist; skills reference no unimplemented
  tool names and no absolute paths.
- Missing: side-effect registry (doc-13 explicitly prefers a registry over frontmatter
  fields); lint for paths/secrets/links; adapter manifest format (WP-320's interface);
  uniform body quality (draft-replies, bootstrap-session, curate-knowledge thinnest).
- Frontmatter parsing in tests uses `split('---', maxsplit=2)` — mis-parses when a
  frontmatter *value* contains `---` (body-level `---` lands safely in the third segment).

## Plan consistency

- Consistent: ADRs 0001-0004, architecture overview, reference-system doc, context-layout
  guide, backlog↔dependency-graph, quickstart hedging.
- Drift: top-level `validate` alias undocumented in doc-04 (D-012); quickstart
  `agent install --context` flag not in doc-04 (pin before WP-320); WP-220/WP-300 prose vs
  JSON dependency mismatches and missing WP-320→WP-330 MCP edge (D-014: JSON authoritative);
  concepts.md "task signal" vs schema enum "task" (WP-001 fixes); last wave labeled
  "Release" in prose vs wave 5 in JSON (cosmetic).
- Open architecture decisions without ADRs: audit ledger representation (D-019), first
  MCP tool surface (D-020).

## Verification pass — accepted findings (2026-07-30)

- All five contracts initially forbade writing their own report files → fixed: each
  contract's allowed_paths now includes its report files; forbidden globs narrowed.
- WP-100 lacked named files for the entity-type enum and observation/claim models its
  acceptance required → fixed: vocabulary.py, observations.py, claims.py added to scope,
  allowed_paths, and the ownership map.
- WP-100↔WP-110 vocabulary anchoring was circular → fixed by D-018 fixed list.
- WP-120 silently dropped the doc-06 "context resolution shell" → restored to scope with
  --context support and a documented step-3 seam; errors.py additive-only freeze made
  contractual; WP-000 added to dependencies.
- ADR 0006 was unsound (no fencing after stale takeover; no write-ahead intent record —
  audit event lands after the replace sequence; Windows sharing-violation retries
  unspecified; torn heartbeat rewrites) → amended with nonce identity, pre-commit
  re-verification, staged intent journal, bounded retry policy, and unparseable-lock rule.
- ADR 0005 underspecified byte determinism (emitter width/indent, None emission,
  free-form key ordering, PyYAML version sensitivity) → amended with exact parameters.
- ADR 0007 backup/lock ordering ambiguity → amended: lock first, then backup.
- ADR 0008 overclaimed drift detection by positive fixtures → amended to require negative
  fixtures per divergence class and to record the live schema_version const-vs-ge example.
- Status docs: stale D-ranges, stale gate counts, missing audit artifact (this document),
  brief-vs-register contradiction on backlog updates, 'planned' status vocabulary →
  corrected.
