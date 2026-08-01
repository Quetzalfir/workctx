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
