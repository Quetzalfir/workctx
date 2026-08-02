# Leader review addendum: `WP-320-agent-installers` — Round 3 (final)

## Decision

`accepted`

(Rounds 1-2 with the blocker resolutions live in `leader-review.md`.)

## Contract compliance

- Base `2c47821` (the D-032..D-034 decisions commit). The worker session finished with
  a clean gate but produced no delivery commit; its report honestly recorded
  `final_commit == base_commit`. The lead captured the reviewed worktree state as
  delivery commit `5fa377e` on the agent branch (documented in the commit message) —
  content byte-identical to what was reviewed.
- Path audit: the two modified files are exactly the granted schema and spec doc; all
  new content sits in granted trees (adapters/agents, resources/agent_kit,
  tests/agents_setup, scripts/sync_agent_kit.py, docs/reference/agent-adapters.md).

## Diff review

- D-032 three-factor authority implemented and adversarially tested: missing or
  tampered trusted record → report-only; modified manifest target → whole repair and
  uninstall go report-only; plan revalidation aborts before mutation when a skipped
  user-owned bridge changed after intent.
- D-033 source sets: order-independent serialization, domain-separated hash framing,
  Codex packaged sources seeded native-verified and retained on uninstall.
- D-034 kit bridges: target-flavored, kit-authored, conditional on an existing
  AGENTS.md, no repo-development references; existing user bridges preserved and never
  recreated once the user deletes them.
- Credential negative protections present; MCP seam remains `not_implemented` as
  contracted (lead finalizes against WP-330's server identity at wave close).

## Validation executed

| Command | Result | Notes |
| --- | --- | --- |
| report.json vs agent-report.schema.json | pass | validated by lead |
| `uv run ruff check .` / format / mypy | pass | 79 source files |
| `uv run pytest` | pass | 1181 passed, 6 recorded Windows skips |

## Findings

- Delivery-commit omission: process note only; the report's honest commit fields made
  the state auditable. Future prompts already require commits — worth reinforcing in
  Wave 4 prompt templates.

## Integration notes

- Integrated after WP-330 (disjoint trees). MCP-config finalization is the lead's
  wave-close task.
