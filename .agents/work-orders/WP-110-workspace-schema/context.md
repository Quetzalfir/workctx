# Work-order context: WP-110-workspace-schema

## Why this exists

The workspace template and its schemas are the canonical-data contract. Today only context
config has a typed model; the template is never validated against the schemas that describe
it, drift already exists (context.schema.json does not require created_at/updated_at while
ContextConfig does), and two byte-identical template trees exist with no declared canonical
side. WP-200, WP-210, WP-220, and WP-400 are blocked on these contracts.

## Required architecture and decisions

- ADR 0005: PyYAML, model field order, LF — your serialization contract.
- ADR 0007: single integer workspace `schema_version` — your versioning contract.
- ADR 0008: schemas hand-maintained; alignment via shared fixtures.
- D-006/D-007 (`.agents/status/decision-register.md`): frozen `__init__` files and frozen
  `WorkctxUri` API; import `WorkctxUri` from `workctx.models.reference` (stable shim path).
- D-010: schema file ownership split — you own entity/task/context/artifact-manifest/
  transaction-proposal/audit-event; WP-100 owns reference/source-locator/observation/claim.

## Existing implementation

- `src/workctx/models/context.py` — ContextConfig + enums; invariants pinned
  (security_boundary const isolated, languages.repository const en).
- `src/workctx/services/contexts.py` — slugify, resolve_context_root (walk-up),
  load_context_config, initialize_context (empty-dir guard, importlib.resources copytree,
  rewrites 'example-context' literals in md/yaml/json).
- `src/workctx/resources/context_template/` — full canonical tree (00_inbox …99_meta),
  zone READMEs, template AGENTS.md, policies.yaml, source-catalog.yaml, and only two
  frontmatter templates: task.md, evidence.md.
- `templates/context/` — byte-identical mirror enforced by tests/test_template_sync.py.
- Schemas exist for all owned documents; tests/test_schemas.py only meta-validates
  (Draft202012Validator.check_schema) — $refs are never dereferenced, so a broken $ref
  passes CI today.
- Frozen timestamps in 99_meta/templates/*.md leak into new workspaces by design
  (authoring placeholders); context.yaml timestamps are correctly overwritten on init.

## Dependencies

- WP-001 baseline: green gate; `src/workctx/domain/__init__.py` exists (frozen).
- WP-100 runs in parallel and owns the reference-side schemas. The entity-type vocabulary
  anchor for BOTH orders is decision D-018 (fixed 19-value list in
  `.agents/status/decision-register.md`) — test against that literal list; never read
  WP-100's branch. If your reading of doc-03 disagrees, raise it to the lead; do not
  negotiate by editing shared files.

## Known risks and edge cases

- `_replace_template_context_id` rewrites every md/yaml/json containing 'example-context' —
  never use that literal as prose in new template files.
- Wheel packaging of `src/workctx/resources/context_template` relies on hatchling defaults
  and may interact with VCS-ignore handling (root `.gitignore` has `**/98_state/*`; the
  template ships its own `.gitignore`). WP-001 adds a built-wheel content check; keep your
  template additions verifiable there.
- `ruff` `extend-exclude` covers `templates/context/98_state` only — if you add fixture
  content under the packaged `98_state`, mirror the exclusion need to the lead.
- transaction-proposal and audit-event schemas are deliberately loose; WP-300 owns their
  tightening. Mark TODO boundaries instead of guessing semantics.
- `tests/test_cli.py` asserts current `context validate --json` shapes — do not break it;
  CLI shapes belong to WP-120.
- New test directories (`tests/workspace/`) need an `__init__.py`: pytest's default import
  mode collides on duplicate basenames against the flat `tests/test_*.py` files otherwise.
- Known drift you fix: `context.schema.json` pins `schema_version` to `const: 1` while the
  model accepts `ge=1` — align per your contract scope (model rejects != 1 with a
  migration-required error).
