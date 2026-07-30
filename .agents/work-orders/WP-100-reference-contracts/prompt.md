# Worker assignment: `WP-100-reference-contracts`

You are the worker assigned to `WP-100-reference-contracts` in the Work Context OS
repository. You are working in the Git worktree `.worktrees/WP-100-reference-contracts`
on branch `agent/WP-100-reference-contracts`.

## Mandatory instructions

1. Read `AGENTS.md` at the repository root.
2. Read `.agents/work-orders/WP-100-reference-contracts/contract.json`, `context.md`, and
   `acceptance.md` in this work-order directory.
3. Read every file listed under `required_reading` in the contract.
4. Work only in the assigned worktree and branch.
5. Modify only `allowed_paths`; never modify `forbidden_paths`. In particular:
   `src/workctx/domain/__init__.py` and `src/workctx/models/__init__.py` are frozen —
   import new symbols from their defining modules.
6. The `WorkctxUri` public API (`parse`, `__str__`, `require_context`) is frozen: extend,
   do not break. Existing tests in `tests/test_reference.py` must pass without semantic
   changes.
7. Keep all repository artifacts in English. Communicate with the human operator in the
   language configured in `.agents/operator.local.yaml` when present.
8. Do not expand scope or change architecture silently. A blocker is a valid result.
9. Add tests for every behavior; run every validation command in the contract.
10. Before stopping, write `report.md` and `report.json` in this work-order directory,
    following `.agents/templates/work-order/report.md` and `report.json`, including exact
    commands and results. A completion claim without executed command evidence will be
    rejected.

## Objective

Implement the reference model of `.agents/plan/initial/03-reference-and-retrieval-model.md`
as typed domain code: ID families (`domain/ids.py`), canonical URI (move to
`src/workctx/domain/references.py` with a compatibility shim at
`src/workctx/models/reference.py`), `artifact://` and `repo://` source references
(`references.py`), the 9 source-locator types with ordering validation (`locators.py`),
the typed-relation enum (`relations.py`), the D-018 19-value entity-type enum
(`vocabulary.py`), and typed Observation/Claim models (`observations.py`, `claims.py`) —
aligned with the four reference JSON Schemas you own (`reference`, `source-locator`,
`observation`, `claim`) through shared positive and negative fixtures per ADR 0008.

## Validation commands

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

If safe completion requires missing information, forbidden paths, or an architectural
change, stop and report a blocker instead of improvising.
