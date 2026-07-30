# Work-order context: WP-130-skill-contract

## Why this exists

All 13 planned skills exist and validate against the minimal frontmatter contract, but
doc-13's real machinery is missing: no side-effect classification registry, no lint for
paths/secrets/links, no adapter manifest format for drift detection, and uneven skill
bodies. WP-320 (agent installer, Wave 3) consumes the registry and manifest formats this
order defines.

## Required architecture and decisions

- `.agents/plan/initial/13-skill-and-agent-adapter-design.md` — the contract you implement.
  Line ~87 explicitly prefers a registry over non-portable frontmatter fields.
- `.agents/skills/README.md` — canonical-source rule.
- D-006 etc. do not affect you: you own no src/ files this wave.

## Existing implementation

- All 13 skills at `.agents/skills/<name>/SKILL.md`; frontmatter is name+description only;
  `tests/test_skills.py` (1 test) checks schema validity, folder/name match, uniqueness.
- `schemas/skill-frontmatter.schema.json` — minimal portable contract with
  additionalProperties:false (frozen for you).
- Bridges exist: AGENTS.md (Codex), CLAUDE.md, GEMINI.md, .github/copilot-instructions.md.
  CLAUDE.md already anticipates generated .claude/skills/ copies — no such copies exist and
  none may be generated until the manifest lands.
- Skills contain zero workctx CLI or MCP tool references today (verified by audit) and no
  absolute paths.
- Body quality is uneven: draft-replies has only a Procedure section; bootstrap-session
  lacks explicit side-effect/approval statements; process-evidence and lead-implementation
  are the strongest examples to normalize toward.

## Dependencies

- WP-001 baseline only. No file overlap with WP-100/110/120 — your surface is .agents/skills,
  two new schema files, skill tests, and one new doc.

## Known risks and edge cases

- The current test parses frontmatter with text.split('---', maxsplit=2). Because of
  maxsplit, a '---' in the BODY lands safely in the third segment; the real failure mode is
  a '---' inside a frontmatter VALUE (YAML string), which shifts the split boundary and
  truncates the frontmatter. Write the regression test against that case and use a real
  frontmatter parse (YAML between the first two '---' lines).
- The test loads JSON Schema via yaml.safe_load; switching to json.loads is a legitimate
  cleanup within your paths.
- Some skill descriptions lack negative scope ("Do not use for...") — doc-13 requires
  descriptions that distinguish nearby workflows; fix while improving bodies.
- Keep the registry small and factual: id, side_effect_class, optional notes. Resist adding
  trigger metadata there that duplicates descriptions; the description remains the trigger
  source of truth.
- jsonschema is available in the dev dependency group for tests; add no new dependencies.
