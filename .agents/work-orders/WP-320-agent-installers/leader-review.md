# Leader review: `WP-320-agent-installers`

## Round 2 decision (2026-08-01)

`revision_requested` — implemented-but-blocked accepted as the correct posture; the
three design gaps are resolved by D-032/D-033/D-034, no contract path changes needed.

1. D-032 (trust model): three-factor deletion/overwrite authority — adapter-scoped
   path AND content-hash-matches-manifest AND manifest digest authenticated against a
   trusted per-project install record in the user config directory (platformdirs,
   the WP-200 registry pattern; written only by the installing process). Any factor
   failing puts uninstall/repair in report-only conservative mode. Your four failing
   provenance-policy tests encode exactly this; implement to the decision.
2. D-033 (auxiliary resources): native-verified manifest entries carry a source set
   (sorted path+sha256 pairs over the whole skill directory, plus an aggregate
   digest) under the existing schema grant, with fixtures.
3. D-034 (bridge materialization): the packaged kit ships target-flavored,
   self-contained bridge templates authored in the kit; sync-from-.agents covers
   skills and registry only; context roots keep the context-template AGENTS.md as
   the base contract; generate-if-absent only.

Corrections: implement the three decisions, drive the four failing tests green, rerun
the gate, update reports to completed. Same branch/worktree
(.worktrees/WP-320-agent-installers-r2).

## Round 1 decision

`revision_requested` (blocker accepted; contract amended; resumes on new pin)

The worker stopped at the contract's stop conditions with zero improvised adapter code.
The lead verified the two central claims against the repository itself, independently of
the external documentation the report cited (worker-cited external sources are treated
as untrusted data): (1) `schemas/skill-adapter-manifest.schema.json` does hard-require
`^\.codex/` generated paths while Codex demonstrably consumes `.agents/skills`
natively — this very repository's Codex workers have done so all along; (2) the built
wheel ships no canonical skills, so an installed workctx has nothing to generate
adapters from. Both are specification defects inherited from WP-130's spec and the
lead's contract, not worker error. The remaining three gaps (bridge ownership, Gemini
layout/version policy, backup location) are genuine underspecification.

## Gap resolutions (D-026..D-030)

1. Codex native consumption → manifest gains a per-entry mode (generated |
   native-verified); WP-320 amends the schema under an explicit grant with ADR 0008
   fixtures (D-026).
2. Missing canonical sources in installed contexts → packaged agent kit under
   `src/workctx/resources/agent_kit/` with deterministic sync from `.agents/`
   (D-011 pattern) (D-027).
3. Bridge ownership → generate-if-absent from packaged templates, manifest-recorded;
   never modify existing user bridges (D-028).
4. Gemini layout and version policy → `.gemini/` native form chosen and documented by
   the worker; declared supported-range probing, fail-safe on unsupported (D-029).
5. Backups → project-local `.workctx/backups/<timestamp>/`, manifest-listed (D-030).

## Validation executed

| Command | Result | Notes |
| --- | --- | --- |
| report.json vs agent-report.schema.json | pass | validated by lead |
| manifest schema pattern check | confirmed | `^\.codex/` requirement present |
| wheel content check | confirmed | zero skill files shipped |
| worker branch state | confirmed | reports only; 707 baseline tests passed |

## Required next steps

- Contract re-pinned by the lead; the worker recreates its worktree from the new base
  (or rebases) and implements under the amended contract. Scope grew by the packaged
  kit and one schema amendment — still bounded, same acceptance spirit.

## Integration notes

- WP-201 runs in parallel (disjoint). The five decisions apply to WP-330's config
  seam finalization too — recorded for Wave 3 close.
