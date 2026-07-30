# Canonical skills

Each subdirectory contains one portable workflow in `SKILL.md`.

These files are the source of truth. Agent-native copies or generated commands are adapters and must be rebuildable. Keep frontmatter limited to broadly supported fields unless the adapter design explicitly changes.

Skill design and validation requirements are documented in `.agents/plan/initial/13-skill-and-agent-adapter-design.md`.

## Side-effect registry

`registry.yaml` classifies every canonical skill under the portable contract in
[`skill-registry.schema.json`](../../schemas/skill-registry.schema.json). The registry
records the highest permission that the skill itself may exercise. An explicitly
out-of-scope operation does not elevate the class: for example, a workflow that produces a
draft but never delivers it remains `local_proposal`.

The classes are `read_only`, `local_proposal`, `local_mutation`, `external_read`, and
`external_write`. Any future `external_write` entry must describe its explicit-approval
boundary in `notes`, beginning with `Requires explicit approval` (or the equivalent schema-
accepted phrase `Explicit approval is required`). Approval to inspect, draft, or mutate
locally never authorizes a send, publish, transition, merge, push, or other remote change.

## Generated adapters

No adapter copies under `.claude/`, `.gemini/`, `.codex/`, or another client directory are
generated in this phase. That begins only when WP-320 implements the manifest contract in
[`skill-adapters.md`](../../docs/reference/skill-adapters.md). Generated files are disposable
projections; this directory remains the only canonical skill source.
