# Work-order context: WP-220-validation-engine

## Why this exists

Today `validation/workspace.py` checks structure only (directories, UTF-8, secrets,
absolute paths, federated_search). Nothing validates documents against the typed
contracts, resolves references, or checks task/claim semantics — the checks doc-03
requires before large-scale ingestion. WP-300 preconditions reuse your rules.

## Required architecture and decisions

- doc-03 "Validation rules" section is your rule catalog; doc-08 defines the secret/path
  controls you inherit.
- The file was frozen during Wave 1 precisely so you could rebuild it now; the CLI
  consumes `validate_workspace(root) -> ValidationReport` with `.ok/.errors/.warnings`
  and issue fields `severity/code/message/path` — that consumed surface is frozen
  (see `src/workctx/presentation/` and `src/workctx/cli.py` for the exact usage; you may
  read them, not modify them).
- ADR 0008: runtime validation is Pydantic domain models; jsonschema stays dev-only.
- D-018: the entity-type vocabulary check uses `workctx.domain.vocabulary.EntityType`.

## Existing implementation

- `src/workctx/validation/workspace.py`: Severity/ValidationIssue/ValidationReport
  dataclasses, REQUIRED_DIRECTORIES, secret patterns, absolute-path detection — evolve,
  do not discard; existing codes (CTX-*) should remain stable.
- Domain models and `validate_task_hierarchy` are integrated — import from
  `workctx.domain`; `workctx.domain.frontmatter` parses documents.
- `tests/test_validation.py` pins current behavior — extend it; existing assertions keep
  passing.
- `tests/workspace/` fixtures (WP-110) are a ready source of valid documents to mutate
  into negative cases inside your own `tests/validation_engine/`.

## Dependencies

- WP-100/WP-110 integrated on your base. WP-200/WP-210 run parallel and are
  file-disjoint from you; you consume neither — projection freshness goes through your
  own FreshnessProbe protocol with a null implementation this wave.

## Known risks and edge cases

- Entity-id-to-file mapping: filenames and frontmatter ids can disagree — that mismatch
  is itself a diagnostic worth a code.
- Reference resolution needs an entity index; build an in-memory one during the walk
  (no SQLite). Large-workspace performance is NFR-009 territory — keep the walk single
  pass where practical, but correctness wins this wave.
- Claim subject equality is URI string equality after canonical normalization
  (`str(WorkctxUri.parse(...))`).
- `templates/` authoring placeholders inside `99_meta/templates/` are not canonical
  entities — exclude them from document validation but keep structural checks.
- Observation URIs embed `%23`; use the domain normalization helper rather than string
  munging.
- New test directory `tests/validation_engine/` needs an `__init__.py`.
