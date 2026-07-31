# Leader review: `WP-220-validation-engine`

## Decision

`accepted`

## Contract compliance

- Merge base verified: `cf9ebab` (contract base). Delivery `eedb573`, report `47205cc`.
- Changed-path audit: 14 files, all inside `allowed_paths`; no adapter imports anywhere
  in `src/workctx/validation/` (verified — the FreshnessProbe ships with a null
  implementation only, exactly per contract).
- Consumed-interface stability proven the strong way: `tests/test_validation.py` and
  `tests/test_cli.py` pass byte-unmodified (21 tests) against the rebuilt engine.

## Diff review

- Engine split into diagnostics/report/engine/freshness modules behind the stable
  `workspace.py` facade; CTX-* structural codes preserved.
- Rule coverage per doc-03 with negative fixtures: typed-model document validation,
  URI parse/boundary/vocabulary/resolution (external and artifact refs as advisories),
  task hierarchy plus blocks/depends_on contradiction cycles, claim current-overlap and
  supersession acyclicity, filename/frontmatter id mismatch.
- 88 new tests across three suites; code/doc sync test enforces that every emitted
  diagnostic code is documented with a repair action in
  docs/reference/validation-diagnostics.md.
- Secret diagnostics report location only; strict mode escalates at API level.

## Validation executed

| Command | Result | Notes |
| --- | --- | --- |
| report.json vs agent-report.schema.json | pass | validated by lead |
| `uv run ruff check .` | pass | worker worktree, independent run |
| `uv run ruff format --check .` | pass | 221 files |
| `uv run mypy src` | pass | 35 source files, strict |
| `uv run pytest` | pass | 449 passed on the branch |
| `uv run pytest tests/test_validation.py tests/test_cli.py` | pass | 21 passed, files unmodified |

## Findings

- Reported unresolved item (SQLite FreshnessProbe wiring) is contracted lead integration
  work, tracked for the Wave 2 close.

## Required revisions

None.

## Integration notes

- Resequenced: integrated second (before WP-200) — WP-200 has not delivered yet and the
  two orders are file-disjoint by contract, so waiting only increased merge distance.
  Deviation from the planned WP-210 → WP-200 → WP-220 order recorded here and in the
  integration log.
