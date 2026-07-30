# Implementation lead operator guide

This guide describes the human-compatible multi-agent workflow used to build Work Context OS. It works even when worker agents cannot communicate directly.

## Roles

- **Human operator:** selects agents, opens sessions, transfers prompts or reports, and approves material product changes.
- **Implementation lead:** owns planning, delegation, review, integration, and release quality.
- **Worker:** implements one bounded work order.
- **Reviewer:** independently evaluates a high-risk delivery.
- **Integrator:** normally the lead; combines only accepted work.

Direct conversation uses the configured interaction language. Repository artifacts and work-order files are in English.

## Start the lead

Open the repository root in the strongest available agent and use the prompt in `START-HERE.md`. The lead must inspect the repository before generating work orders.

The lead copies `.agents/templates/lead/` into an active status location and records:

- current baseline commit;
- decisions still open;
- work-package status;
- path ownership;
- active worktrees;
- integration order;
- validation state.

## Create a worker worktree

Example:

```powershell
git fetch --all --prune
git worktree add .worktrees/WP-100-reference -b agent/WP-100-reference <BASE_COMMIT>
```

The lead creates:

```text
.agents/work-orders/WP-100-reference/
├── contract.json
├── prompt.md
├── context.md
├── acceptance.md
├── report.md
├── report.json
└── leader-review.md
```

The contract must assign non-overlapping writable paths for concurrent workers.

## Hand a worker to any agent

1. Open the assigned worktree in Codex, Claude Code, Gemini CLI, or another capable coding agent.
2. Paste the contents of `prompt.md`.
3. Let the worker inspect only the required repository context.
4. The worker implements and writes both reports.
5. Return the report to the lead or tell the lead which worktree and commit contain the delivery.

The worker is not allowed to declare architectural changes implicitly. A blocker is a valid result.

## Lead review

The lead must independently:

1. validate `report.json`;
2. confirm base and final commits;
3. inspect every changed file;
4. compare changed paths with the contract;
5. run all acceptance commands;
6. add negative or integration tests where risk warrants them;
7. inspect documentation and migration impact;
8. request an independent reviewer for security, transaction, reference, or cross-platform risk;
9. write `leader-review.md`;
10. decide `accepted`, `revision_requested`, or `rejected`.

A worker report is evidence to inspect, not proof of correctness.

## Revision loop

A revision request must preserve the original bounded scope and state:

- failed acceptance criterion;
- exact evidence;
- correction required;
- commands that must pass;
- whether the same worktree continues;
- any newly discovered dependency that the lead must resolve first.

## Integration

Only accepted commits may integrate. The lead then:

1. integrates in dependency order;
2. resolves conflicts without discarding either contract silently;
3. runs combined quality and acceptance gates;
4. updates status, risks, ADRs, and changelog;
5. marks the work order integrated and then verified;
6. removes the worktree only after the branch is safely retained;
7. reports the result and next wave to the human operator in the configured interaction language.

## Parallelism rules

Parallel work is appropriate only when:

- dependencies are complete;
- public contracts are already stable;
- writable paths do not overlap;
- each result is independently testable;
- combined integration risk is acceptable.

Keep work sequential when a worker is defining an interface, schema, migration, security rule, or transaction behavior required by another worker.
