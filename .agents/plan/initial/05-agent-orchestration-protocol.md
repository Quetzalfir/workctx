# Multi-agent orchestration protocol

The protocol supports two realities:

1. some agents can create native subagents;
2. The human operator may manually open separate Codex, Claude, or Gemini sessions and copy prompts between them.

The process must work in both cases.

## Roles

### Implementation lead

Owns architecture coherence, dependency planning, delegation, review, integration, status, and release quality. The lead does not outsource final judgment.

### Worker

Implements one bounded contract in an assigned worktree and writes a structured report.

### Reviewer

Performs an independent focused review—correctness, security, tests, API, documentation, or architecture—without implementing unless separately authorized.

### Integrator

May be the lead. Resolves accepted branches, runs the full suite, updates status, and records decisions.

## Work-order lifecycle

```text
proposed -> ready -> assigned -> in_progress -> reported
-> review_required -> accepted | revision_requested | rejected
-> integrated -> verified -> closed
```

## Work-order contents

Every worker directory contains:

- `contract.json`: machine-readable scope and constraints;
- `prompt.md`: self-contained prompt the human operator can copy;
- `context.md`: exact required context and references;
- `acceptance.md`: review checklist and commands;
- `report.md`: human report written by worker;
- `report.json`: machine-readable report;
- `leader-review.md`: acceptance or revision decision.

## Contract minimum

- objective and business value;
- explicit scope and non-goals;
- dependencies and base commit;
- allowed and forbidden paths;
- required reading and context references;
- deliverables;
- acceptance criteria;
- commands/tests;
- security constraints;
- migration and documentation expectations;
- output/report location;
- stop conditions.

## Worktree policy

Parallel workers should use:

```text
branch: agent/<work-order-id>-<slug>
worktree: .worktrees/<work-order-id>
```

The lead must create a path ownership matrix. Work with overlapping writable paths is sequential unless the lead deliberately splits interfaces first.

## Delegation decision

Delegate only when the task is:

- bounded;
- independently testable;
- supplied with enough context;
- not a foundational decision still under analysis;
- safe under the assigned permissions.

Keep work sequential when:

- one package defines interfaces required by the next;
- two workers would edit the same files;
- data-model uncertainty could invalidate downstream work;
- migration or security behavior is unresolved;
- integration risk exceeds parallelization value.

## Worker prompt requirements

The prompt must be copyable without hidden chat context. It must include:

- role;
- repository/worktree path assumption;
- task objective;
- required files to read;
- exact scope;
- forbidden changes;
- acceptance criteria;
- test commands;
- required report paths;
- instruction to communicate with the human operator in the configured interaction language and write files in English.

## Worker completion report

The worker must report:

- status: completed, partial, blocked, or failed;
- summary;
- base and final commit;
- files changed;
- behavior implemented;
- tests and exact results;
- assumptions and decisions;
- deviations from contract;
- security and migration considerations;
- unresolved issues;
- recommended next action.

A claim such as "tests pass" without the commands and results is insufficient.

## Lead review gate

The lead must:

1. validate report schema;
2. inspect base commit and diff;
3. check allowed-path compliance;
4. read changed code and docs;
5. run work-order acceptance commands;
6. run relevant regression tests;
7. challenge assumptions and omitted edge cases;
8. request an independent review when risk warrants it;
9. write `leader-review.md`;
10. accept, request revision, or reject.

## Revision

A revision request must be concrete:

- failed criterion;
- evidence of failure;
- required correction;
- unchanged scope;
- commands that must pass;
- whether the same branch/worktree should continue.

## Integration

Only accepted deliveries may integrate. After integration:

- run the combined suite;
- validate schemas and docs;
- update backlog and dependency graph status;
- record ADRs and changelog entries;
- remove or archive worktrees safely;
- provide the human operator a status summary in the configured interaction language.

## Failure and disagreement

When workers disagree, the lead compares evidence, tests, architecture constraints, and current specifications. The lead may assign a narrow reviewer task. Do not settle disagreement by model reputation alone.

## Native subagents

An agent with native subagents may use them, but must still materialize equivalent work orders and reports for important parallel work. Hidden delegation is not a substitute for repository auditability.
