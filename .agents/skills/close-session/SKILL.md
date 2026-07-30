---
name: close-session
description: "Use at the end of a meaningful work session to persist progress, unresolved questions, next actions, drafts, and validation state so a new AI session can continue without the current chat."
---

# Close session

## Procedure

1. Identify canonical changes made during the session.
2. Confirm related transactions and audit entries.
3. Update task status or next actions only when supported by actual work/evidence.
4. Record unresolved questions, blockers, and waiting-on people.
5. Persist useful drafts or investigation notes in their owning task/outbox.
6. Regenerate operational views and validate affected references.
7. Do not save transient chain-of-thought or private hidden reasoning.
8. Produce a handoff for the human operator in the configured interaction language:
   - what changed;
   - what remains;
   - validation status;
   - next best action;
   - where the persistent context now lives.

## Success criterion

A fresh agent session can recover the work state from canonical context without this conversation.
