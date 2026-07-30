# Work-order context: WP-001-dev-foundation

## Why this exists

The scaffold shipped with lint, format, and typing violations and no line-ending policy.
The validation gate is the trust anchor for every later worker report; it must pass from a
clean checkout on all supported platforms before any Wave 1 delegation.

## Required architecture and decisions

- `AGENTS.md` validation gate section.
- `.agents/plan/initial/06-implementation-work-packages.md` — WP-001 acceptance.
- ADR 0005 (canonical serialization: LF policy motivates `.gitattributes`).

## Existing implementation

- `pyproject.toml` — complete tooling config (ruff line-length 100 / LF, strict mypy, pytest).
- `.github/workflows/ci.yml` — 3-OS × 2-Python matrix running the full gate; no build check.
- Local fixes already applied in the working tree (2026-07-30): B008 removal in
  `src/workctx/cli.py`, typed `ContextConfig` construction in
  `src/workctx/services/contexts.py`, import ordering, formatting to 100 columns.

## Dependencies

- WP-000 lead baseline (status directory, ADRs 0005-0008, work orders) — same commit series.

## Known risks and edge cases

- `core.autocrlf=true` on the operator machine: without `.gitattributes * text=auto eol=lf`,
  a fresh Windows checkout writes CRLF and `ruff format --check` fails repository-wide.
- `uv.lock` is intentionally not ignored by `.gitignore`; committing it is required for
  reproducible CI.
- This order executes on `master` by the implementation lead because it produces the baseline
  commit that Wave 1 worktrees branch from; there is no parallel work to conflict with.
