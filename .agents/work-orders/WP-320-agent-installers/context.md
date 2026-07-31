# Work-order context: WP-320-agent-installers

## Why this exists

The product promise is agent neutrality: canonical skills and rules serving Codex,
Claude Code, and Gemini CLI without drift. WP-130 defined the manifest/drift contract;
you implement the machinery. The definition of done requires install/status/repair/
uninstall that never destroys user-owned configuration.

## Required architecture and decisions

- doc-13 adapter strategy per client and the ten installation requirements are your
  functional spec; its five acceptance scenarios map to your acceptance criteria.
- WP-130's `docs/reference/skill-adapters.md` + `schemas/skill-adapter-manifest.schema.json`
  define the manifest exactly — consume, never redefine. The spec was written so you
  need no new design decisions; if that fails, coordination request.
- D-014: MCP config generation waits for WP-330's server identity. Reserve the manifest
  field, report NOT-IMPLEMENTED in status, and leave the seam documented.
- The repo's own `.gitignore` already ignores `.claude/skills/`, `.gemini/commands/`,
  `.codex/config.local.toml` — generated adapters are per-project artifacts, not
  committed content, in workctx-managed projects too.

## Existing implementation

- WP-130 (integrated): registry.yaml (13 skills classified), manifest schema with
  unsafe-path rejection, lint tests. The documented manifest example is itself
  schema-validated in tests — mirror that pattern.
- Detection precedent: `src/workctx/doctor.py` does PATH-based executable checks
  (read it; do not modify it).
- Test strategy doc-07 "Agent adapter tests" section: isolated fake home directories
  and fake executable discovery; idempotent reinstall; repair after user edits;
  uninstall preserving user files; no credentials copied.

## Dependencies

- WP-120/WP-130 integrated long since; you run parallel to WP-300 with zero path
  overlap. Nothing you produce depends on transactions; adapters are generated files
  plus a manifest, not canonical workspace mutations.

## Known risks and edge cases

- Windows path handling for client directories; keep everything project-scoped and
  relative in the manifest (the schema rejects unsafe/absolute generated paths).
- Idempotency must be content-based (hashes), not timestamp-based.
- A user may hand-edit a generated adapter: status must flag it as drift (hash
  mismatch against BOTH canonical source and recorded generated hash — distinguish
  'canonical changed' from 'user edited' if the manifest allows; document whichever
  the spec supports).
- open_context must not inherit/forward secrets in env beyond the parent environment
  as-is; no env var injection.
- New test directory `tests/agents_setup/` needs an `__init__.py`.
