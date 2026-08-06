# Brief: WP-760 — Adapter recovery, freshness, and merge surfacing (C-215)

Codex worker, worktree `.worktrees/WP-760`, branch `agent/WP-760-adapter-freshness`.
You cannot commit; leave changes uncommitted. Final message = report.
`.agents/` read-only — read C-215 (+ addendum) in
`.agents/status/phase2-candidates.md` FIRST, then study
`src/workctx/adapters/agents/` (_install_records, service plan/authority
logic, the WP-690 three-way override marker) and the WP-750 registry hook in
`src/workctx/services/contexts.py`.

## Four pieces (operator-approved as one package)

1. `workctx agent forget [PATH]` (defaults to the resolved context): remove
   that context's entry from the machine-local trusted install record ONLY —
   never touches context files. Idempotent; clear human/JSON output stating
   what a subsequent `agent install` will treat as untracked. This is the
   official exit from trust divergence (today it required hand-editing the
   JSON, and the three-factor check blocks even uninstall — write a test
   reproducing exactly that circular state and proving forget + fresh
   install recovers it).
2. Pristine-skill freshness: context-canonical `.agents/skills/**` files are
   codex-manifest-tracked generated output, but rendering treats the context
   copy as the source, so packaged-skill improvements never reach existing
   contexts. Contract: when a tracked skill file's bytes still match its
   manifest record (operator never edited it), `agent install` must plan a
   REPLACE from the CURRENT packaged kit; an operator-edited file is
   preserved and surfaced (piece 4), never replaced. The registry.yaml and
   newly-added packaged skills follow the same rule (new skills appear).
3. Register-on-use: after successful context resolution in the CLI boundary,
   best-effort register the context (id + resolved root) when absent or
   moved. MUST be swallow-to-nothing on any failure and add no measurable
   overhead when already registered. HAZARD (proven live): fictional test
   contexts polluted the operator's real registry. Before the hook lands,
   add suite-wide registry isolation in the ROOT tests/conftest.py (autouse:
   point the registry path and user-config dir into the test tmp tree),
   and remove any now-redundant per-file isolation you find conflicting.
4. Merge surfacing (NO auto-merge, NO new transaction machinery): when a
   managed file (bridge, canonical skill) is BOTH operator-edited and behind
   the current packaged version, `agent status` and the install plan must
   surface the three-way state (recorded-at-adoption hash, packaged-now
   hash, local hash) with the file path — mirroring the WP-690 override
   marker. Plus a short "Merging your edits with updates" section in
   docs/guides/personalization.md explaining the operator/agent flow: agent
   drafts the merged file, operator approves, file returns to tracked state
   on the next install.

## Do NOT touch

Anything outside: `src/workctx/adapters/agents/**`,
`src/workctx/adapters/filesystem/registry.py` (additive),
`src/workctx/services/contexts.py`, `src/workctx/cli.py` (agent group +
the resolution hook), `tests/conftest.py` (isolation fixture only),
`tests/agents_setup/**`, `tests/cli/**`, `tests/filesystem/test_registry.py`,
`docs/guides/personalization.md`, `docs/reference/agent-adapters.md`,
`docs/reference/cli-envelope.md` (rows). No pyproject, no schemas beyond
what the manifest schema already allows, no MCP.

## Tests required

Forget: idempotency, circular-trust recovery end to end, registry-only
effect. Freshness: pristine skill updated to new packaged content; edited
skill preserved; new packaged skill appears; registry.yaml refresh. Register
-on-use: registers once, re-registers on move, never fails a command when
the registry is unwritable, and the SUITE-WIDE isolation proves no test
touches the real user registry (assert via a canary). Merge surfacing:
three-way hashes exact, present in status and plan JSON. Full gate; declare
sandbox limits explicitly; the existing 1666 tests must stay green.
