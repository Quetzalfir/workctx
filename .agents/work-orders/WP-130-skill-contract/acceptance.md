# Acceptance criteria: WP-130-skill-contract

## Functional

- [ ] `.agents/skills/registry.yaml` classifies all 13 skills; validates against
      `schemas/skill-registry.schema.json`.
- [ ] Lint covers: absolute machine paths, secret-like values, broken internal links,
      robust frontmatter parsing, registry completeness both directions.
- [ ] `docs/reference/skill-adapters.md` + `schemas/skill-adapter-manifest.schema.json`
      define generated paths, sha256 content hash, adapter version, and staleness semantics.
- [ ] draft-replies, bootstrap-session, and curate-knowledge bodies follow the uniform
      doc-13 section contract; approval boundaries are explicit.

## Negative and edge cases

- [ ] A skill with `---` inside a frontmatter value parses correctly (regression for the
      split-boundary shift); a body-level `---` also stays safe.
- [ ] An unclassified skill or orphan registry entry fails tests.
- [ ] A fixture skill with an absolute path, a secret-like value, or a broken link fails
      lint (test with temporary fixtures, not by committing bad skills).
- [ ] A skill referencing an unimplemented workctx command without a 'planned' marker
      fails lint.

## Quality

- [ ] Frontmatter stays name+description; skill-frontmatter.schema.json untouched.
- [ ] No .claude/.gemini/.codex copies generated; the prohibition is documented.
- [ ] Allowed paths only; evidence-as-untrusted-data language strengthened where thin.
- [ ] No secrets or private data.

## Commands

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```
