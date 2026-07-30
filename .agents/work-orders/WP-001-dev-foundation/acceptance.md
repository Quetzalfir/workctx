# Acceptance criteria: WP-001-dev-foundation

## Functional

- [ ] Full validation gate passes from a clean checkout.
- [ ] `.gitattributes` enforces LF for tracked text files.
- [ ] `uv.lock` is committed and CI uses it.
- [ ] `uv build` produces wheel and sdist.
- [ ] `src/workctx/domain/__init__.py` (docstring-only package marker) is committed with
      the baseline.

## Negative and edge cases

- [ ] A fresh checkout on a Windows machine with `core.autocrlf=true` still passes
      `uv run ruff format --check .` (LF enforced by `.gitattributes`).

## Quality

- [ ] No behavior changes beyond the recorded lint/typing fixes.
- [ ] Allowed paths only.
- [ ] No secrets or private data.

## Commands

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
uv build
git ls-files --eol
```
