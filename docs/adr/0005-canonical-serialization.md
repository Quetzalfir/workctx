# ADR 0005: Canonical serialization and frontmatter ordering

- Status: accepted
- Date: 2026-07-30

## Context

Canonical Markdown/YAML files are the source of truth (ADR 0001). Diffs must stay small and
deterministic so Git history remains a usable audit trail, and multiple agents must produce
byte-identical output for identical logical state. The architecture plan requires an early
decision on the serialization library and key-ordering policy.

## Decision

- Use PyYAML `safe_load` / `safe_dump` for all canonical YAML, including frontmatter.
- Exact emitter parameters (byte determinism requires pinning them, not just the library):
  `sort_keys=False`, `allow_unicode=True`, `default_flow_style=False`, `indent=2`,
  `width=4096` (prevents line-wrapping of long scalars such as URLs and source locators,
  which would break grep-ability and inflate diffs).
- Serialize mapping keys in Pydantic model field-declaration order (`sort_keys=False`);
  `schema_version` is always the first declared field on versioned documents.
- Optional/None policy: dump with `model_dump(mode="json")` emitting **all declared fields
  including nulls** (`field: null`), matching the plan's own examples (doc-03 shows
  `valid_to: null`). Omission-vs-null is therefore never a per-writer choice.
- Free-form dict-valued fields (no model declaration order exists) serialize with keys
  sorted lexicographically.
- Encode UTF-8 without BOM, LF line endings, block style.
- Frontmatter is delimited by `---` lines at the top of Markdown entities; the body follows
  one blank line after the closing delimiter.
- Comment preservation is not guaranteed in canonical machine-written files. Files intended
  for human commentary keep prose in the Markdown body, not YAML comments.
- PyYAML's emitter output is only deterministic per library version; the version is pinned
  by `uv.lock`, and a byte-format golden test must accompany any PyYAML upgrade.
- `ruamel.yaml` (round-trip comment support) is deferred; adopting it later requires a
  superseding ADR because it changes byte-level output.

## Consequences

- deterministic output enables projection rebuild equality tests and small diffs;
- model field order becomes part of the public contract and must not be reordered casually;
- user-edited files may carry comments that a canonical rewrite will drop — mutation flows
  must warn before rewriting a hand-edited file; detection mechanism: compare the file's
  bytes against canonical re-serialization of its parsed content — a mismatch means
  hand-edited (WP-300 implements this check at transaction staging);
- no new runtime dependency is introduced.
