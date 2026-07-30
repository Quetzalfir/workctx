# Worker report: `WP-120-cli-envelope`

## Status

`completed`

## Summary

Implemented the doc-04 CLI presentation boundary. Every parsed JSON-capable command now
returns the shared, schema-validated result envelope; runtime failures are mapped through a
single top-level boundary with clean stdout, sanitized stderr diagnostics, and the exact
lead-decided exit codes. Context selection supports explicit `--context`, retained
positional paths, ancestor discovery, and a documented WP-200 registry seam. ADR 0008
schema/model fixtures, rewritten split-stream tests, and public reference documentation are
included.

## Base and final commits

- Base: `ea6861f956326c35fbbca2bcaf276f423e08570f`
- Final implementation: `b4e7752d337a917d3096ca999e5d09446dbc6f41`

## Files changed

- `src/workctx/cli.py`
- `src/workctx/errors.py`
- `src/workctx/presentation/__init__.py`
- `src/workctx/presentation/boundary.py`
- `src/workctx/presentation/context.py`
- `src/workctx/presentation/envelope.py`
- `src/workctx/presentation/streams.py`
- `schemas/cli-envelope.schema.json`
- `docs/reference/cli-envelope.md`
- `tests/test_cli.py`
- `tests/cli/__init__.py`
- `tests/cli/test_envelope_contract.py`
- `tests/cli/test_exit_codes.py`
- 14 positive and negative fixtures under `tests/cli/fixtures/envelope/`
- `.agents/work-orders/WP-120-cli-envelope/report.md`
- `.agents/work-orders/WP-120-cli-envelope/report.json`

## Behavior implemented

- Strict envelope fields: `ok`, `command`, `context_id`, object-shaped `result`, structured
  `warnings` and `errors`, and `meta.schema_version` plus `meta.duration_ms`.
- Root `PresentationTyperGroup` catches expected and unexpected runtime exceptions once,
  emits sanitized failures, and maps success/user/usage/boundary/conflict/dependency/
  partial/internal bands to 0/1/2/3/4/5/6/10.
- Typer retains native pre-command usage handling on stderr with exit 2.
- `doctor`, `context init`, `context inspect`, `context validate`, and top-level `validate`
  return the envelope in JSON mode; `doctor.result` is `{"checks": [...]}` and `version`
  remains plain text.
- Human output uses a stdout console; failure diagnostics use a distinct stderr console.
- `--context` overrides a positional path and ancestor discovery. Failed explicit paths do
  not silently fall back. The final missing-context error is clear.
- Human `context init` output and its JSON result expose the resolved target.
- Existing error classes remain byte-for-byte unchanged; new exit-band classes are
  additive.
- Invalid configuration/parser errors and unexpected failures use stable generic text.
  Known diagnostics are single-line, bounded, and secret-redacted.
- The hand-maintained Draft 2020-12 schema and Pydantic model are aligned by positive and
  negative ADR 0008 fixtures, including JSON integer-number semantics.
- Tests assert split stdout/stderr behavior, every exit band, all command envelopes,
  ancestor and explicit resolution, alias behavior, result object shape, and secret-safe
  failure output.

## Validation executed

| Command | Result | Notes |
| --- | --- | --- |
| `uv run ruff check .` | pass | `All checks passed!` |
| `uv run ruff format --check .` | pass | `151 files already formatted` |
| `uv run mypy src` | pass | `Success: no issues found in 19 source files` |
| `uv run pytest` | pass | `66 passed in 1.84s` |
| `uv run workctx context inspect --help` | pass | Installed entry point exposes `--context` and `--json` |
| Independent read-only code and contract reviews | pass | No remaining actionable findings after two corrections and regression tests |

## Assumptions and decisions

- The operator's explicit assignment and supplied ready contract supersede the
  base-commit copy's stale `proposed`/`PENDING-WAVE0-BASELINE` metadata. Those files are
  forbidden and were not modified.
- Native Typer parsing failures keep empty stdout, usage text on stderr, and exit 2 because
  no parsed JSON command exists yet; parsed runtime failures always use the envelope.
- The retained top-level `validate` alias reports canonical command identity
  `context.validate`.
- The Pydantic companion models live under the allowed presentation package because
  `src/workctx/models/**` is frozen.

## Contract deviations

None.

## Security and migration considerations

- No dependencies, network access, external writes, secrets, or canonical workspace schema
  changes were introduced.
- Unexpected exceptions never expose raw text, reprs, causes, locals, or tracebacks.
- Aggregated `CTX-CONFIG` parser diagnostics are replaced with stable generic text before
  entering either `result.issues` or `errors`.
- No migration is required; the new schema governs CLI output only.

## Unresolved issues

None.

## Recommended next action

The implementation lead should inspect commit `b4e7752d337a917d3096ca999e5d09446dbc6f41`,
validate both reports, rerun the four acceptance commands, and accept or request revision.
