---
name: lead-implementation
description: "Use when acting as the implementation lead for this repository: planning dependencies, creating work orders, coordinating workers, reviewing deliveries, integrating accepted changes, and reporting status. Do not use for a small isolated code change with an existing approved work order."
---

# Lead implementation

## Purpose

Coordinate a verifiable multi-agent implementation without losing architectural coherence or accepting unverified worker claims.

## Required inputs

- repository root;
- `AGENTS.md`;
- current implementation plan and backlog;
- active work orders and reports;
- current Git status and branch graph.

## Procedure

1. Bootstrap by reading the plan and current status.
2. Identify dependencies, unresolved decisions, and writable path conflicts.
3. Decide sequential versus parallel execution using evidence, not model availability.
4. Create bounded work orders from `.agents/templates/work-order/`.
5. Recommend a branch and Git worktree for each worker.
6. Give the human operator a self-contained copyable worker prompt and explain it in the configured interaction language, while keeping prompt files in English.
7. Track work-order state and blockers.
8. On delivery, validate the report schema and inspect the actual diff.
9. Run the contract's commands independently.
10. Request independent review for security, transaction, reference, migration, or cross-platform risk.
11. Write `leader-review.md` with acceptance or revision evidence.
12. Integrate only accepted work and run combined regression gates.
13. Update backlog, ADRs, risks, changelog, and status.
14. Report progress and next actions to the human operator in the configured interaction language.

## Invariants

- no overlapping writable paths in parallel assignments;
- no foundational decision delegated before the lead understands it;
- no completion based only on a worker narrative;
- no silent architecture change;
- no private data in public fixtures;
- no Phase 2 UI work during Phase 1 unless the human operator explicitly changes scope.

## Output

- work-order files;
- path ownership map;
- dependency/status updates;
- review records;
- human status summary in the configured interaction language.
