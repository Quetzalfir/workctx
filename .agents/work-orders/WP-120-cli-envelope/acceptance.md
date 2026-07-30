# Acceptance criteria: WP-120-cli-envelope

## Functional

- [ ] Envelope builder produces {ok, command, context_id, result, warnings, errors,
      meta{schema_version, duration_ms}} for every --json command.
- [ ] result is always an object; doctor emits {"checks": [...]}.
- [ ] doctor, context inspect, context validate, top-level validate alias, and context init
      all emit the envelope in JSON mode; version stays plain text.
- [ ] context init supports --json and prints the resolved context target in human mode.
- [ ] All envelopes validate against schemas/cli-envelope.schema.json in tests.

## Negative and edge cases

- [ ] JSON mode: stdout is exactly one parseable JSON document even on failure; human
      diagnostics arrive on stderr (tests assert with split streams).
- [ ] Exit codes: invalid context = 1; usage error = 2 (Click preserved); doctor
      required-check failure = 5; injected unexpected exception = 10 with a sanitized
      envelope error.
- [ ] The exit-code mapper has direct unit tests for every band (0-6, 10), including
      reserved codes 3, 4, and 6 that no current command triggers.
- [ ] --context with an explicit path overrides ancestor discovery; a missing context
      fails with the doc-04 step-4 clear error; the step-3 registry seam is documented.
- [ ] workctx.errors existing classes are unchanged (new classes additive only).

## Quality

- [ ] Presentation logic lives in new module files; command bodies shrink to
      resolve-call-serialize.
- [ ] No changes to services/, validation/, models/, domain/ — consumed via frozen
      interfaces only.
- [ ] docs/reference/cli-envelope.md documents envelope, exit codes, and the kept
      validate alias.
- [ ] Allowed paths only; no secrets.

## Commands

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```
