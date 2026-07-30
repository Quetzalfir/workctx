# Worker assignment: `WP-110-workspace-schema`

You are the worker assigned to `WP-110-workspace-schema` in the Work Context OS repository.
You are working in the Git worktree `.worktrees/WP-110-workspace-schema` on branch
`agent/WP-110-workspace-schema`.

## Mandatory instructions

1. Read `AGENTS.md` at the repository root.
2. Read `.agents/work-orders/WP-110-workspace-schema/contract.json`, `context.md`, and
   `acceptance.md` in this work-order directory.
3. Read every file listed under `required_reading` in the contract.
4. Work only in the assigned worktree and branch.
5. Modify only `allowed_paths`; never modify `forbidden_paths`. In particular:
   `src/workctx/domain/__init__.py`, `src/workctx/models/__init__.py`, the four
   WP-100-owned schemas (reference, source-locator, observation, claim), and
   `src/workctx/validation/**` are frozen for you.
6. The public signatures of `initialize_context`, `load_context_config`,
   `resolve_context_root`, and `slugify_context_id` are frozen during Wave 1: extend
   behavior without breaking callers (`src/workctx/cli.py` and the validation module
   consume them).
7. Import `WorkctxUri` only from `workctx.models.reference` (stable shim path).
8. Keep all repository artifacts in English. Communicate with the human operator in the
   language configured in `.agents/operator.local.yaml` when present.
9. Do not expand scope or change architecture silently. A blocker is a valid result.
10. Add tests for every behavior; run every validation command in the contract.
11. Before stopping, write `report.md` and `report.json` in this work-order directory,
    following `.agents/templates/work-order/` templates, with exact commands and results.
    A completion claim without executed command evidence will be rejected.

## Objective

Turn the workspace template and its schemas into validated contracts: typed entity/task/
artifact models aligned with their JSON Schemas through fixtures (ADR 0008), schema drift
fixed (context created_at/updated_at; entity_type + observation/artifact), task hierarchy
rules in code, template instance validation with resolved $refs, missing entity templates
added, and one canonical template tree with a deterministic sync to the public mirror.

The entity-type vocabulary anchor is decision D-018 in `.agents/status/decision-register.md`
— test against its literal 19-value list, never against another branch's files.

## Validation commands

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

If safe completion requires missing information, forbidden paths, or an architectural
change, stop and report a blocker instead of improvising.
