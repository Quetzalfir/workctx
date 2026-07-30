---
name: investigate-system
description: "Use for an architecture, integration, security, operational, or code investigation that must combine repository evidence, documents, systems, flows, decisions, and optional live connectors."
---

# Investigate system

## Procedure

1. Define the question, decision to support, and required confidence.
2. Retrieve related systems, flows, repositories, tasks, decisions, risks, and prior investigations.
3. Separate known product ownership, integration/orchestration, and external domains.
4. Prefer primary sources:
   - current code and committed configuration;
   - approved architecture or API contracts;
   - authoritative system queries;
   - direct evidence from responsible people.
5. Use optional architecture or code plugins only when available and validate important findings against source material.
6. Record each finding as fact, inference, or assumption with exact references.
7. Identify contradictions, stale documentation, missing access, and owner uncertainty.
8. Produce:
   - findings;
   - impact;
   - recommended change or decision;
   - probable owner;
   - validation plan;
   - risks and unanswered questions.
9. Keep task-specific notes under the owning investigation/task until reusable.
10. Persist durable findings and proposed work transactionally.
