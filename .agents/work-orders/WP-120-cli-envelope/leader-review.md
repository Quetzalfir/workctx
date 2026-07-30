# Leader review: `WP-120-cli-envelope`

## Decision

`accepted`

Integration deliberately deferred: per the Wave 1 integration order (first-wave brief),
WP-120 merges last because it rewrites the CLI tests other integrations should not race.
It merges after WP-110 and WP-130 are integrated.

## Contract compliance

- Base commit matches (`ea6861f`); delivery `b4e7752`, report `74a5d61` on
  `agent/WP-120-cli-envelope`.
- Changed-path audit: 29 files, all inside `allowed_paths`; frozen files untouched;
  `services/contexts.py`, `validation/workspace.py`, and models consumed through public
  interfaces only.
- `errors.py` verified additive-only by diff: six new classes (UserCorrectable,
  UsageConfiguration, ContextBoundary, Conflict, UnavailableDependency,
  StaleDerivedState) mapping onto exit bands 1-6; existing classes byte-identical.

## Diff review

- New `src/workctx/presentation/` package (envelope, streams, context resolution shell,
  boundary) — command bodies in `cli.py` reduce to resolve-call-serialize as contracted.
- Exit-code mapper implements the D-015 table exactly, with a dedicated
  `test_exit_code_mapper_covers_every_band` including reserved 3, 4, 6, and 10.
- Envelope schema is strict (additionalProperties false, integral duration_ms,
  result-must-be-object, ok/errors consistency) with 4 positive and 10 negative ADR 0008
  fixtures — negative cases rejected by BOTH schema and model.
- stdout purity and stderr routing tested with split streams; secret-looking diagnostic
  content sanitized (tests inject a fake token and assert absence).
- `--context` precedence over positional and ancestor discovery tested; step-4 clear
  failure tested; step-3 registry seam documented in docs/reference/cli-envelope.md.
- validate alias reports canonical `context.validate` command identity (D-012 honored).

## Validation executed

| Command | Result | Notes |
| --- | --- | --- |
| report.json vs agent-report.schema.json | pass | validated by lead |
| `uv run ruff check .` | pass | worker worktree, independent run |
| `uv run ruff format --check .` | pass | 151 files |
| `uv run mypy src` | pass | 19 source files, strict |
| `uv run pytest` | pass | 66 passed (baseline was 21) |

## Findings

- Worker's assumption that the operator-supplied ready contract supersedes the branch's
  pre-pin metadata copy is correct (pin landed in `b1a006d` after the branch point).
- Expected merge interaction at integration time: none at file level with WP-110/WP-130;
  behavioral check (envelope over WP-110's extended validation output) covered by the
  combined regression gate at merge.

## Required revisions

None.

## Integration notes

- Merge after WP-110 and WP-130; rerun the full combined gate; then consolidate any
  duplicate presentation/domain validators flagged by WP-100's report.
