---
name: create-work-order
description: "Use when the implementation lead needs to delegate a bounded implementation or review task to another AI agent through a self-contained prompt and contract."
---

# Create work order

## Procedure

1. Choose a stable `WP-###-slug` ID.
2. Confirm dependencies and base commit.
3. Select allowed paths that do not overlap with concurrent workers.
4. State objective, scope, non-goals, deliverables, and stop conditions.
5. List exact required reading and context references.
6. Define observable acceptance criteria and commands.
7. Create the worktree/branch recommendation.
8. Copy `.agents/templates/work-order/` into `.agents/work-orders/<ID>/`.
9. Populate and validate `contract.json` against the schema.
10. Make `prompt.md` self-contained for manual copy/paste.
11. Ensure report paths and language rules are explicit.
12. Mark the order `ready` only after a lead review.

## Quality rules

- avoid vague requests such as "implement the architecture";
- separate implementation and independent review when risk is high;
- do not grant repository-wide write access without necessity;
- include exact expected failure behavior, not only happy paths;
- require tests and documentation in the same contract where applicable.
