# Agent work orders

Large implementation work uses written contracts under `.agents/work-orders/`.

The lead creates the work order, the human operator copies the worker prompt to a selected agent, and the worker operates in a dedicated branch/worktree. The worker writes a report; the lead inspects the diff and runs tests independently.

This protocol makes the implementation auditable even when agents cannot communicate directly.

See `.agents/plan/initial/05-agent-orchestration-protocol.md`.
