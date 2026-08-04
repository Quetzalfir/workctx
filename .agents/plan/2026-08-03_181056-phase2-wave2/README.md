# Phase 2 — Wave 2 (operator-approved 2026-08-03)

Operator approved the cut: C-211 secrets, C-201 personalization, C-209 repo
guide + curation tiering. Baseline: the wave-1 close plus the lead's keyring
dependency commit (D-043) and layer-location decision (D-044).

## Packages (all parallel, disjoint)

| Package | Candidate | Scope | Worker |
| --- | --- | --- | --- |
| WP-620 | C-211 (+addendum) | Secret references: resolver, CLI, .env import | Codex (max effort) |
| WP-630 | C-201 | Personalization layers merged into agent bridges | Codex (max effort) |
| WP-640 | C-209 | Code-repositories guide + three-tier curation rule in skills | Claude agent |

## Path ownership (disjoint)

- WP-620: `src/workctx/secrets/**` (new), `src/workctx/cli.py` (secret group +
  lazy import only), `tests/secrets/**` (new), `docs/reference/secrets.md`
  (new), `docs/reference/cli-envelope.md` (command-table rows only).
- WP-630: `src/workctx/adapters/agents/**`, `tests/agents_setup/**`,
  `docs/guides/personalization.md` (new).
- WP-640: `src/workctx/resources/agent_kit/skills/**`,
  `docs/guides/code-repositories.md` (new), `docs/guides/multiple-contexts.md`
  (one cross-link line only).

## Reserved for the lead

- ADR 0013 (secret resolver design) — WP-620 drafts the content in its report;
  the lead authors and ratifies the ADR at integration.
- pyproject stays frozen (keyring already added by the lead).

## Wave-close criteria

1. Full gate + CI matrix green with all three integrated.
2. WP-620: values never appear in workspace, envelopes, logs, or reports —
   proven by tests; `secret set` masked-interactive; `import` shreds on
   confirm; env fallback works keyring-less.
3. WP-630: one instructions layer written once reaches all three agent
   bridges; upgrades preserve user layers (three-factor records).
4. WP-640: skill lint stays green; tier rule present in both skills and the
   guide; no unimplemented-command references without "(planned)".
