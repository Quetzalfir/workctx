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
| 2026-08-03 | LEAD-W5-lock-views-fixes | lead fix | direct | lock TOCTOU (macOS CI) + 04_views recreation + regression test | committed 62282bf | found by full-ci + WP-520 |
| 2026-08-03 | WP-520-acceptance | capture f031b9a | merge 4ca2cba | combined gate 1446 tests | accepted+integrated | 5 e2e scenarios; 1 product blocker (04_views) fixed by lead |
| 2026-08-03 | WP-510-migration | capture 8f1cc7c | merge 684ef8d | worktree gate 1449 tests; lead reran outside sandbox | accepted+integrated | D-042 single_import; ledger seam kept |
| 2026-08-03 | WP-530-release-docs | capture | merge 47283d0 | agent gate 1455 tests + packaging install-test; lead vocab sweep clean | accepted+integrated | Wave 5 complete |
| 2026-08-03 | LEAD-W5-cli-envelope | lead fix | direct | context-resolution truth + alias wording in cli-envelope.md | committed with close | file was read-only for WP-530 |
| 2026-08-03 | LEAD-W5-inbox-ux | lead fix | direct | stray-path inbox add message + CLI test; full gate 1456 | committed ed028a2 | closes the carried Wave 4 note |
| 2026-08-03 | RELEASE-0.1.0-alpha | tag b4c1d4a | gh release | matrix 6+build green (dispatch 30858233782); wheel+sdist attached | published (private repo) | operator option 1 |
| 2026-08-03 | LEAD-P2-read-apis | lead addition | direct 04248c1 | query_entities + read_audit_events + 3 tests | committed | unblocked WP-600 same-day |
| 2026-08-03 | LEAD-P2-view-count-fix | lead fix | in WP-600 branch | migration view-count anchored to ViewName enum | committed c221fa0 | prevents recurrence |
| 2026-08-03 | WP-600-phase2-views | capture 5b88036 | merge | combined gate 1462; lead reran outside sandbox | accepted+integrated | C-203+C-207 live; 1 blocker round (read APIs) |
| 2026-08-03 | WP-610-performance | capture | merge 5f4a9e8 | combined gate 1463; lead re-measured 4s->1.05s, 133->9 fsyncns, zero fsync sites removed | accepted+integrated | C-208; suite runtime -30% |
| 2026-08-03 | WP-620-secrets | capture 7898b40 | merge 0289e43 | full gate 1511 in worktree; lead forensic review (SecretValue, argv, index lock); ADR 0013 authored | accepted+integrated | lead fix: tests/secrets renamed (stdlib shadowing, 314 fails) |
| 2026-08-03 | WP-630-personalization | capture cc39515 | merge 479001d | focused 297 in worktree; combined gate 1523 on master | accepted+integrated | adapter v3; sandbox ran zero test bodies, lead ran all |
| 2026-08-03 | WP-640-repo-guide | capture aa1d3fb | merge | worker full gate 1463 own-branch; wave gate 1523 | accepted+integrated | .agents/skills mirror deviation accepted; lead freshness fix on secrets mentions |
| 2026-08-03 | LEAD-P2W2-layout-guard | lead addition | direct | tests/test_layout.py bans stdlib-shadowing test packages | committed | prevents the 314-failure class permanently |
| 2026-08-03 | WP-650-more-views | capture 748be61 | merge d07e50d | worktree gate 1527; lead reran outside sandbox | accepted+integrated | 11 views total; C-204/205/206 + C-202 detection |
| 2026-08-03 | WP-660-perf-leftovers | capture | merge | worktree gate 1528; combined 1532; 9 resolution cuts lead-reviewed | accepted+integrated | batch inbox add 1 lock/1 refresh; C-213 debt recorded |
| 2026-08-04 | WP-680-suggestions | capture | merge 923c2ca | worktree gate 1552; atomicity lead-reviewed | accepted+integrated | investigation as URI carrier noted |
| 2026-08-04 | WP-690-overrides | capture | merge 3e83be8 | gate 1543 after 2-test fix round + lead schema patch | accepted+integrated | three-way markers verified |
| 2026-08-04 | WP-710-connectors | capture | merge 633b6c1 | worktree gate 1573; secret containment lead-reviewed | accepted+integrated | 1 blocker round (D-049) |
| 2026-08-04 | LEAD-P3-connector-cli | lead addition | direct 50407f0 | connector list/sync CLI + envelope rows + test | committed | completes Phase 3 wave 1 plan |
| 2026-08-04 | WP-700-telemetry | capture | merge 4abca91 | worktree gate 1565; combined 1618 | accepted+integrated | 1 blocker round (telemetry config prerequisite); engine_proposal carrier noted |
| 2026-08-04 | CI-BILLING-NOTE | n/a | n/a | matrix run 30887534416 failed at job START: Actions minutes/billing exhausted (macOS 10x multiplier); zero steps ran | recorded | validation of record for 5e90876 is the local combined gate (1618 tests); full-ci moratorium until the operator decides billing/visibility |
| 2026-08-04 | LEAD-clock-bound-apply | lead fix | direct 0ace889 | suggestion record + ledger event share one timeline; fixes wall-clock flip-flop in usage decay test | committed | found by first free public matrix; confirmed green 6+build |
| 2026-08-04 | REPO-PUBLIC | operator decision | gh repo edit | employer refs genericized; operator.local never in history; CI re-enabled (free) | done | full-ci discipline stays: releases/dispatch only |
| 2026-08-04 | DEFECT-template-bridge | operator validation of a real context | pending fix | template-shipped AGENTS.md is treated as user-owned at agent install, so the codex bridge never regenerates and personalization is not merged for codex in ANY fresh context | recorded | fix: recognize pristine-template bridge files by content hash as generated-equivalent; lead follow-up after Phase 3 close |
| 2026-08-04 | WP-720-outbox-send | capture | merge 9d7144a | worktree gate 1639; ADR 0014 authored+accepted | accepted+integrated | first external write in product history |
| 2026-08-04 | WP-730-browser-capture | capture | merge 4ac3055 | own-branch full suite 1618; skill lint 390 | accepted+integrated | 14th skill; scope extension for EXPECTED_SKILL_IDS |
| 2026-08-04 | LEAD-install-all-fix | lead fix | direct e2a016e | agent install --agent all targets detected clients; 2 regression tests | committed | operator-found defect |
| 2026-08-04 | WP-740-scheduled-sync | capture | merge | worktree gate 1652 | accepted+integrated | PHASE 3 COMPLETE |
| 2026-08-05 | PHASE3-MATRIX-VERDICT | dispatch | run green 6+build, 0 failures | final tree: Phase 3 close + template-bridge healing + runner-safety round (lead tests stubbed, 8.3 path normalization) | CLOSED | local 1655; matrix confirms cross-platform |
| 2026-08-06 | RELEASE-0.2.0-alpha | tag df8727d | gh release | local gate: ruff+format+mypy 0, pytest 1666 passed; wheel smoke (version+CLI); matrix 31111322318 green 6+build | published (public repo) | Phase 2+3 scope; schema unchanged; wheel+sdist attached |
| 2026-08-06 | WP-760-adapter-freshness | capture d08e51d | merge 1c31073 | worker gate partial (sandbox ACL); lead full gate outside sandbox: ruff+format+mypy 0, pytest 1683 passed | accepted+integrated (1 defect round) | C-215 all 4 pieces; canary caught MCP-subprocess registry leak -> WORKCTX_CONTEXT_REGISTRY fence + lead env-inherit fix in the SDK test |
| 2026-08-07 | WP-770-fleet-refresh | capture | merge | worker gate partial (sandbox ACL); lead full gate outside sandbox: ruff+format+mypy 0, pytest 1690 passed | accepted+integrated | C-216; agent refresh --all; exit band 6 wording extended for failure-isolated batches |
| 2026-08-07 | WP-780-bridge-hardening | capture | merge | worker gate partial (sandbox ACL); lead reconciled canonical bootstrap-session mirror, full gate outside sandbox: ruff+format+mypy 0, pytest 1696 passed | accepted+integrated | C-218 orient-first + ask-once; content tests pin the contract phrases |
| 2026-08-17 | WP-790-ownership-guide | capture | merge | worker gate partial (sandbox ACL); lead reconciled bootstrap-session mirror + wording polish, full gate outside sandbox: ruff+format+mypy 0, pytest 1709 passed | accepted+integrated | C-219; workctx guide + bridge discovery sentence |
| 2026-08-18 | LEAD-C221-template-history | lead fix | direct | full gate: ruff+format+mypy 0, pytest 1711 passed | committed | historical template hashes; guard test forces append-on-change |
| 2026-08-18 | WP-800-skill-commands | capture 244b0b8 | merge 0983e56 | lead gate: 1719 passed; mirror sync + template hash appended (C-221 guard) + section-contract unification | accepted+integrated | realignment wave 1/3 |
| 2026-08-18 | WP-810-validation-realignment | capture | merge db9de8c | lead gate: 1735 passed; mirror sync, META-SCHEMA-STALE registered+documented | accepted+integrated (1 defect round) | realignment wave 2/3; poison fix |
| 2026-08-18 | WP-820-custom-skills | capture | merge | lead gate: 1741 passed (worker sandbox ran zero pytest bodies — lead ran all); three-path code review | accepted+integrated | C-220 delivered |
| 2026-08-21 | LEAD-msix-diagnosis | lead fix | direct | full gate: ruff+format+mypy 0, pytest 1748 passed; doctor check live-verified against a real container | committed | operator-agent-found: secret list vs check split; Codex shadow purged (held Aug-3 trust record) |
