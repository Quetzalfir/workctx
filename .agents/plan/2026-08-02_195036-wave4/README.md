# Wave 4 — 2026-08-02

Per D-040 (continuous pipeline). Briefs in this directory; contracts remain the
work-order files for WP-4xx governance; lead batches run on briefs alone.

| Package | Worker | Branch | State |
| --- | --- | --- | --- |
| WP-400 tasks/claims/views | Codex gpt-5.6-sol max | agent/WP-400-tasks-views | running |
| WP-410 evidence workflow | Codex gpt-5.6-sol max | agent/WP-410-evidence-workflow | running |
| LEAD-W3 inbox CLI | Codex gpt-5.6-sol max | lead/inbox-cli | running |
| DOCS-R1 refresh | Claude agent (worktree) | (agent worktree) | running |
| WP-420 drafting/outbox | — | — | proposed; launches on WP-400 integration |

Disjointness: WP-400 (tasks/, views/, tests/tasks_views, views.md) · WP-410
(evidence/, mcp/application.py narrow, tests/evidence + tests/mcp new file,
evidence-workflow.md) · W3 (cli.py, tests/cli new file, cli-envelope.md) · DOCS-R1
(README/CHANGELOG/ROADMAP/guides/concepts + 4 stale reference docs, cli-envelope
excluded). Verified no path overlaps.

Integration order on delivery: WP-410 → WP-400 → W3 → DOCS-R1 → cut WP-420.
