---
name: bootstrap-session
description: "Use at the start of a user work session inside a Work Context workspace to recover current focus, tasks, blockers, recent evidence, waiting-on relationships, and stale state before asking the user to repeat context."
---

# Bootstrap session

## Procedure

1. Resolve and state the active context ID.
2. Confirm context validation and projection freshness.
3. Load the generated operational brief or build it from canonical tasks and claims.
4. Retrieve:
   - active P0/P1 work;
   - blocked and waiting-on work;
   - recent reliable evidence;
   - pending inbox artifacts;
   - unanswered questions;
   - stale current claims or views;
   - pending outbox drafts and approvals.
5. Retrieve only the related entity context needed for the user's immediate request.
6. Do not ask the human operator for information that already exists in the context.
7. Present a concise briefing in the configured interaction language and the next best action.

## Output

- active context;
- current priorities;
- blockers and people awaited;
- inbox and validation status;
- recommended first action.

## Stop conditions

Stop and report before normal work when:

- the context cannot be resolved;
- validation reports critical corruption;
- a write lock appears unsafe or stale and cannot be recovered;
- context configuration indicates a different security boundary than expected.
