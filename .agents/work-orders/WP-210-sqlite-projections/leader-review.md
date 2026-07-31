# Leader review: `WP-210-sqlite-projections`

## Decision

`accepted`

## Contract compliance

- Merge base verified: `cf9ebab` — exactly the contract's base_commit. (Diffing against
  the later pin commit `a066d87` shows the pin's own files; the true delivery diff is
  clean.) Delivery `c23db02`, report `c7d6820`.
- Changed-path audit from the true base: 12 files, all inside `allowed_paths`; the frozen
  parent `src/workctx/adapters/__init__.py` untouched; no services/validation/presentation
  edits; stdlib sqlite3 only (no pyproject changes).

## Diff review

- `adapters/sqlite/` package: schema DDL with projection metadata (projection schema
  version + workspace schema version + build metadata), typed query APIs, temp-build-and-
  swap rebuild with adapter-managed reader closing before `os.replace` (the Windows
  handle hazard addressed explicitly), and per-document skip-and-report.
- Acceptance coverage verified in tests: rebuild equivalence (twice and after database
  deletion), projection AND workspace version-mismatch full rebuild, exact backlink
  mirroring, FTS5 with documented tokenizer, two-context denial, malformed-document
  skip with sanitized reporting, reader old-then-new swap visibility.
- Beyond-contract hardening accepted as in-scope robustness: symlink/sidecar escape
  denial (fail-closed), orphaned-sidecar refusal, FTS5-unavailable typed error mapping
  to the stop-condition, transient PermissionError retry on swap, fail-closed schema
  context guards.
- 99_meta/templates and non-canonical zones excluded from indexing per context.md.

## Validation executed

| Command | Result | Notes |
| --- | --- | --- |
| report.json vs agent-report.schema.json | pass | validated by lead |
| `uv run ruff check .` | pass | worker worktree, independent run |
| `uv run ruff format --check .` | pass | 221 files |
| `uv run mypy src` | pass | 35 source files, strict |
| `uv run pytest` | pass | 392 passed (Wave 2 baseline was 351) |

## Findings

- The projection intentionally re-validates via domain models and skips foreign-context
  structured references without partial rows — good isolation depth.
- No incremental writes, per the WP-300 boundary.

## Required revisions

None.

## Integration notes

- Integrated first in the Wave 2 order; WP-230's base pins to the post-merge master.
- FreshnessProbe wiring (WP-220) happens at WP-220's integration.
