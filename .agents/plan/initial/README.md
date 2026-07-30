# Initial implementation plan

This directory is the authoritative starting plan for the implementation lead.

## Status

- Plan state: `proposed`
- Target release: `0.1.0-alpha`
- Product phase: `Phase 1 — CLI and durable core`
- Repository language: English
- Human communication language: configured locally; English fallback

## Required reading order

1. `00-executive-brief.md`
2. `01-product-scope.md`
3. `02-architecture.md`
4. `03-reference-and-retrieval-model.md`
5. `04-cli-specification.md`
6. `05-agent-orchestration-protocol.md`
7. `06-implementation-work-packages.md`
8. `07-test-strategy.md`
9. `08-security-and-privacy.md`
10. `09-open-source-distribution.md`
11. `10-migration-from-legacy-repo.md`
12. `11-risk-register.md`
13. `12-definition-of-done.md`

Machine-readable companions:

- `dependency-graph.json`
- `initial-backlog.json`
- `agent-task-contract.schema.json`
- `agent-report.schema.json`

Prompts:

- `leader-start.txt` — plain-text prompt for the first lead session
- `leader-system-prompt.md`
- `worker-prompt-template.md`
- `reviewer-prompt-template.md`

Visual reference:

- `reference-system.html`

## Authority and change control

The plan may be refined after implementation discovery, but the lead must not silently change product invariants. Material changes require:

1. a documented reason;
2. impact analysis;
3. an ADR under `docs/adr/`;
4. updated dependencies and acceptance criteria;
5. an explanation to the human operator in the configured interaction language.

## Lead output expected before delegation

The implementation lead must create:

- a current-state assessment;
- a first-wave execution proposal;
- work-order directories under `.agents/work-orders/`;
- a conflict-free path ownership map;
- validation gates for each work order;
- an integration and rollback plan.

The lead must not mark a work package complete solely because a worker says it is complete.
