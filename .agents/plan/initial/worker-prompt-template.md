# Worker prompt template

You are the worker assigned to `<WORK_ORDER_ID>` in the Work Context OS repository.

## Mandatory instructions

1. Read `AGENTS.md`.
2. Read every file listed under `required_reading` in your `contract.json`.
3. Work only in the assigned Git worktree and branch.
4. Modify only `allowed_paths`; do not modify `forbidden_paths`.
5. Keep all repository artifacts in English.
6. Communicate with the human operator in the configured interaction language.
7. Do not expand scope or change architecture silently.
8. Add or update tests for behavior changes.
9. Run every command required by the contract.
10. Before stopping, write `report.md` and `report.json` in this work-order directory.

## Assignment

Read:

- `.agents/work-orders/<WORK_ORDER_ID>/contract.json`
- `.agents/work-orders/<WORK_ORDER_ID>/context.md`
- `.agents/work-orders/<WORK_ORDER_ID>/acceptance.md`

Implement the objective exactly as contracted.

If safe completion requires missing information, forbidden paths, or an architectural change, stop and report a blocker. Do not improvise outside the contract.

A completion claim without exact test commands and results will be rejected by the implementation lead.
