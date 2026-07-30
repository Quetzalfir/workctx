# Acceptance criteria: WP-110-workspace-schema

## Functional

- [ ] Typed models exist for entity frontmatter, task, and artifact manifest; each has an
      ADR 0008 fixture validating against its JSON Schema and round-tripping the model.
- [ ] context.schema.json requires created_at/updated_at and its schema_version rule
      matches the model (model rejects versions != 1 with a migration hint); positive and
      negative fixtures guard alignment.
- [ ] entity_type enum equals the D-018 19-value list exactly (tested against the literal
      list from the decision register).
- [ ] Task hierarchy rules (parent tasks self-rooted, subtasks point to existing parents,
      TASK-YYYY-NNN / -STNN grammar) are enforced with negative tests.
- [ ] Template instance validation runs in tests with resolved $refs (registry), covering
      context.yaml and all frontmatter templates.
- [ ] New frontmatter templates exist for at least person, decision, risk, question, claim.
- [ ] One template tree is declared canonical (packaged copy) and the mirror is
      generated/synced deterministically; the rule is documented.

## Negative and edge cases

- [ ] A template document violating its schema fails the test suite.
- [ ] A broken $ref between schemas fails the test suite (no silent pass).
- [ ] Subtask with a missing parent, or parent with a foreign root, is rejected.
- [ ] context init on a non-empty directory still fails safely (existing test preserved).

## Quality

- [ ] services/contexts.py public signatures unchanged; existing init/inspect/validate
      tests pass unmodified except where contracts legitimately extend them.
- [ ] Allowed paths only; frozen files untouched.
- [ ] Fictional data only; no secrets; invariants (isolated boundary, federated_search
      false, repository language en) intact.

## Commands

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```
