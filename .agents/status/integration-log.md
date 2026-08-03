# Integration log

| Date | Work order | Accepted commit | Integrated commit | Validation | Result | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-07-30 | WP-100-reference-contracts | 4766cbf | 5c9cd03 | ruff+format+mypy+pytest (148 passed) | accepted+integrated | First Wave 1 integration; combined gate green |
| 2026-07-30 | WP-110-workspace-schema | ce06415 | c577801 | ruff+format+mypy+pytest (222 passed) + D-018 cross-check | accepted+integrated | Second Wave 1 integration |
| 2026-07-30 | WP-130-skill-contract | 64aa455 | 6dfba1c | ruff+format+mypy+pytest | accepted+integrated | Third Wave 1 integration |
| 2026-07-30 | WP-120-cli-envelope | 74a5d61 | 788aa1c | Final combined gate: 344 passed + uv build + wheel check + CLI smoke | accepted+integrated | Wave 1 complete |
| 2026-07-30 | WP-210-sqlite-projections | c7d6820 | 0343911 | ruff+format+mypy+pytest (392) | accepted+integrated | First Wave 2 integration |
| 2026-07-30 | WP-220-validation-engine | 47205cc | 7b6e775 | ruff+format+mypy+pytest combined | accepted+integrated | Resequenced before WP-200 (disjoint, WP-200 undelivered) |
| 2026-07-30 | WP-200-canonical-store | 33648d7 | facfa8d | ruff+format+mypy+pytest (623 combined) | accepted+integrated | Third Wave 2 integration |
| 2026-07-30 | WP-230-context-packs | 0f5059a | d64bcab | ruff+format+mypy+pytest (707 combined) | accepted+integrated (1 revision round, ADR 0011) | Wave 2 delivery complete; ref/context-pack CLI wiring pending |
| 2026-08-01 | WP-201-staging-extensions | 8a87193 | 17b1cf8 | ruff+format+mypy+pytest (777 combined) | accepted+integrated | D-024 unblock; pre-existing tests byte-identical |
| 2026-08-01 | WP-300-transaction-engine | 5ff9ce5 | f2bc4f9 | ruff+format+mypy+pytest (938 combined) | accepted+integrated (2 blocker rounds) | Critical path; releases WP-310/WP-330 |
| 2026-08-02 | WP-330-mcp-server | 8bd7681 | a4d8d21 | ruff+mypy(87 files w/ mcp extra)+pytest (1298 combined) | accepted+integrated | ADR 0012 surface live |
| 2026-08-02 | WP-320-agent-installers | 5fa377e (lead-captured) | 243147c | same combined gate | accepted+integrated (3 rounds) | D-026..D-034 machinery |
| 2026-08-02 | WP-310-inbox-lifecycle | capture | merge | ingestion+filesystem+transactions targeted (392) then combined | accepted+integrated (2 blocker rounds) | D-035/D-036 architecture |
| 2026-08-02 | LEAD-W1-cli-wiring | capture | merge | full gate 1384 + coverage 85% | accepted+integrated | 9 commands; 2 stale lint expectations updated at integration |
| 2026-08-02 | LEAD-W2-mcp-seam | capture | merge | full gate 1344 on branch | accepted+integrated (1 correction round) | ownership-set fix via codex resume |
| 2026-08-02 | WP-400-tasks-views | capture | merge | targeted 174 + combined 1418 | accepted+integrated | Wave 4 |
| 2026-08-02 | WP-410-evidence-workflow | capture | merge | combined 1418; D-041 schema completion | accepted+integrated (1 blocker round) | inbox_list/artifact_register live |
| 2026-08-02 | LEAD-W3-inbox-cli | capture | merge | targeted 204 | accepted+integrated | 4 commands |
| 2026-08-02 | DOCS-R1-refresh | capture | merge | docs-only | accepted+integrated | claims inventory closed |
| 2026-08-02 | LEAD-W4-brief-view-cli | capture | merge | targeted 61 cli | accepted+integrated | doc-04 alpha CLI complete |
| 2026-08-02 | WP-420-drafting-outbox | capture | merge | branch gate 1436; D-041 draft_save live | accepted+integrated | Wave 4 complete |
