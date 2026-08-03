# Brief: WP-530 — Release docs and packaging verification

Claude agent, own worktree, branch `agent/WP-530-release-docs`. Launches AFTER
WP-510 integrates so docs describe integrated truth. Final message = report.

## Scope

1. Public docs completion pass (accuracy over polish):
   - README.md: alpha status, install (uv/pipx from source), the six-step cycle
     with real commands, link map into docs/.
   - docs/guides/quickstart.md: walk the full cycle exactly as shipped.
   - NEW docs/security-and-privacy.md: evidence quarantine model, approval gates,
     secret policy (never stored; detection = refusal), context isolation
     boundary, what workctx does NOT protect against. Source material:
     `.agents/plan/initial/08-security-and-privacy.md`, ADRs 0006/0010/0012 —
     public voice, no internal WP/wave vocabulary.
2. Release notes: NEW `docs/releases/0.1.0-alpha.md` — features shipped, known
   limitations (incremental projection updates post-alpha; MCP stdio only;
   migration caveats from WP-510's report), upgrade/stability policy (forward-only
   migrations, ADR 0007).
3. CHANGELOG.md: move Unreleased → `0.1.0-alpha` section (date left as
   UNRELEASED marker; the lead stamps it at tag time).
4. Packaging verification (verify + report, do NOT publish anything):
   `uv build`; inspect wheel+sdist contents (templates, schemas, kit present);
   install the wheel into a fresh venv; run `workctx version`, `workctx context
   init`, `workctx doctor` from the installed package outside the repo.
   Record exact commands + outputs in the report.

## Do NOT touch

`src/**`, `tests/**`, `schemas/**`, `.agents/**` (read-only), pyproject version
(lead stamps it), anything owned by WP-510/WP-520. No new claims about
unimplemented behavior — every statement must be executable today.

## Acceptance

Docs build no broken relative links (check them); English only;
`grep -RE "WP-[0-9]{3}|Wave [0-9]" README.md docs/ --include="*.md"` clean of
internal process vocabulary (except ADR references, which are public);
full gate still green.
