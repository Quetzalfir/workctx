---
name: trace-context
description: "Use when answering a question that requires broader related context or proof: trace tasks, claims, decisions, people, systems, or conclusions through typed relationships to exact evidence locators."
---

# Trace context

## Procedure

1. Resolve the focal entity or query to canonical URIs.
2. Load the focal canonical entity and current claims.
3. Traverse direct typed relations first.
4. Include one-hop related work, people, decisions, risks, and systems only when relevant.
5. Retrieve supporting, contradictory, and superseding observations.
6. Trace observations to exact source locators.
7. Rank by reliability, current validity, recency, confidence, and directness.
8. Separate current truth, historical context, inference, and unresolved questions.
9. State when a reference is unavailable, external, or less precise than desired.
10. Answer with bounded context rather than dumping every related document.

## Quality gate

A trace is successful when another agent can follow the returned URIs to reproduce the reasoning without depending on the current chat.
