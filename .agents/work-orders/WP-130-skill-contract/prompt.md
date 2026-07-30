# Worker assignment: `WP-130-skill-contract`

You are the worker assigned to `WP-130-skill-contract` in the Work Context OS repository.
You are working in the Git worktree `.worktrees/WP-130-skill-contract` on branch
`agent/WP-130-skill-contract`.

## Mandatory instructions

1. Read `AGENTS.md` at the repository root.
2. Read `.agents/work-orders/WP-130-skill-contract/contract.json`, `context.md`, and
   `acceptance.md` in this work-order directory.
3. Read every file listed under `required_reading` in the contract.
4. Work only in the assigned worktree and branch.
5. Modify only `allowed_paths`; never modify `forbidden_paths`. In particular
   `schemas/skill-frontmatter.schema.json` is frozen — extra metadata goes in the registry,
   not in frontmatter. You own no `src/` files: linting lives in tests.
6. Keep all repository artifacts in English. Communicate with the human operator in the
   language configured in `.agents/operator.local.yaml` when present.
7. Do not create `.claude/`, `.gemini/`, or `.codex/` adapter copies — the manifest you
   specify is a design for WP-320, not something you generate now.
8. Do not expand scope or change architecture silently. A blocker is a valid result.
9. Add tests for every behavior; run every validation command in the contract.
10. Before stopping, write `report.md` and `report.json` in this work-order directory,
    following `.agents/templates/work-order/` templates, with exact commands and results.
    A completion claim without executed command evidence will be rejected.

## Objective

Implement the portable-skill machinery of doc-13: a side-effect registry
(`.agents/skills/registry.yaml` + schema) classifying all 13 skills, extended lint tests
(paths, secrets, links, robust frontmatter parsing, registry completeness), an adapter
manifest specification (`docs/reference/skill-adapters.md` + schema) concrete enough for
WP-320 to implement drift detection without new design decisions, and uniform improved
skill bodies with explicit approval boundaries.

## Validation commands

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

If safe completion requires missing information, forbidden paths, or an architectural
change, stop and report a blocker instead of improvising.
