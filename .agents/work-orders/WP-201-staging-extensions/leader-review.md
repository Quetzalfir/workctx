# Leader review: `WP-201-staging-extensions`

## Decision

`accepted`

## Contract compliance

- Branch base `0f99073` (the D-024 resolution commit; the pin-only commit `e001692`
  differs solely in contract metadata — administrative deviation accepted, same pattern
  as prior orders). Delivery `abbbb0c`, report `8a87193`.
- Changed-path audit: 6 files, all inside `allowed_paths`.
- Backward compatibility verified by diff: all five pre-existing tests/filesystem test
  files are byte-identical; only `staging.py`, adapter re-exports, one new test module,
  and the reference doc changed.

## Diff review

- `IntentTargetKind` (replace/move/delete) with schema-v1-compatible intent records;
  apply, recovery inspection, completion, and rollback cover all kinds and mixed
  sequences; move refuses existing destinations; delete preserves preimages.
- Fenced append: nonce verified immediately before the write, single-buffer
  torn-line-safe writes, fsync, bounded PermissionError retries, in-boundary parent
  creation, works with an active intent, finalize semantics unchanged.
- 52 new tests in `test_staging_extensions.py` mirroring the original failure-injection
  rigor (mid-sequence per kind, mixed, fence-mismatch abort, escape refusal, torn-line
  injection, old-intent compatibility).

## Validation executed

| Command | Result | Notes |
| --- | --- | --- |
| report.json vs agent-report.schema.json | pass | validated by lead |
| pre-existing test immutability diff | pass | empty diff on all five files |
| `uv run ruff check .` / format / mypy | pass | 296 files / 58 source files |
| `uv run pytest` | pass | 777 passed on the branch |

## Findings

- Residual no-clobber race vs non-cooperating external processes: consistent with ADR
  0006's documented Phase 1 limitation set; recorded, no action.

## Required revisions

None.

## Integration notes

- WP-300 re-pins to the post-integration master and flips back to `ready`; its worker
  resumes in its existing worktree by merging master (WP-230 precedent).
