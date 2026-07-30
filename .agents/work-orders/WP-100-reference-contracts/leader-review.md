# Leader review: `WP-100-reference-contracts`

## Decision

`accepted`

## Contract compliance

- Base commit matches the contract (`ea6861f`); delivery commit `69d8c86`, report commit
  `4766cbf` on `agent/WP-100-reference-contracts`.
- Changed-path audit: 26 files, all inside `allowed_paths`; frozen files
  (`domain/__init__.py`, `models/__init__.py`, WP-110 schemas, `pyproject.toml`) untouched;
  `tests/test_reference.py` passes unmodified.
- Reported deviation (branch copy of contract.json still shows the pre-pin placeholder
  base_commit) is expected: the branch was cut from `ea6861f`, the pin landed in `b1a006d`.
  No action needed.

## Diff review

- `domain/vocabulary.py` equals the D-018 19-value list exactly, in order.
- `domain/references.py`: canonical-encoding round-trip enforcement (`str(parse(x)) == x`),
  strict percent-escape validation, `file://` and absolute-path rejection, lowercase-scheme
  enforcement, repo references require 7-64 hex commit and relative forward-slash paths.
- `WorkctxUri` frozen API preserved (parse/str/require_context signatures and behavior);
  validation was *tightened* (`/` and `\` now rejected inside entity IDs, malformed percent
  escapes rejected). Accepted as a security tightening consistent with the contract's
  traversal constraint; noted as intentional in the worker report.
- `models/reference.py` is a clean re-export shim; old import path works.
- `normalize_workctx_uri` handles the authored `#OBS-NNN` form and validates the
  observation ID grammar.
- `tests/domain/test_contract_alignment.py` implements ADR 0008 precisely: positive
  fixtures validate against schema + model + default-dump re-validation; negative fixtures
  must be rejected by BOTH schema and model; `$refs` resolved through a `referencing`
  Registry.

## Validation executed

| Command | Result | Notes |
| --- | --- | --- |
| report.json vs agent-report.schema.json | pass | validated by lead |
| `uv run ruff check .` | pass | worker worktree, independent run |
| `uv run ruff format --check .` | pass | 156 files |
| `uv run mypy src` | pass | 21 source files, strict |
| `uv run pytest` | pass | 148 passed (baseline was 21) |

## Findings

- Schema tightening on the four owned schemas is deliberate and covered by negative
  fixtures; downstream WP-110 alignment happens at its own integration.
- External URI families remain syntax-only placeholders per D-021 — correctly not expanded.

## Required revisions

None.

## Integration notes

- Integrated first per the Wave 1 integration order (reference vocabulary lands before
  WP-110's entity enum alignment).
- Cross-branch D-018 equality check with WP-110 happens at WP-110's integration.
