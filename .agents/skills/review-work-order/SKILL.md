---
name: review-work-order
description: "Use when a worker has reported completion or a revision and the implementation lead or independent reviewer must verify the actual delivery against its contract."
---

# Review work order

## Procedure

1. Read contract, acceptance criteria, worker report, and prior review history.
2. Validate `report.json` structurally.
3. Confirm base/final commits and inspect the complete diff.
4. Check changed paths against the contract.
5. Evaluate behavior, edge cases, security, migrations, and public API impact.
6. Run every required validation command independently.
7. Run targeted negative and regression tests when the risk justifies them.
8. Check documentation and examples against actual behavior.
9. Classify findings by severity and cite exact files/lines.
10. Write `leader-review.md` with one decision:
   - accepted;
   - revision requested;
   - rejected.
11. Update work-order status only after writing evidence.

## Rejection conditions

- hidden scope expansion;
- missing tests for changed behavior;
- unverified "tests pass" claim;
- context isolation or secret handling regression;
- broken references or schema incompatibility;
- worker modified forbidden paths;
- documentation materially disagrees with implementation.
