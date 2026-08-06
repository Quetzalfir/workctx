# Personalization layers

Work Context can apply the same user-owned instructions to Codex, Claude Code, and Gemini CLI
without maintaining a separate copy for each client. The two optional layers are plain Markdown:

| Layer | Fixed location | Best used for |
| --- | --- | --- |
| User | `instructions.md` beside the user context registry in the platform-specific Work Context configuration directory | Stable tone, working role, communication preferences, and personal boundaries that apply across contexts |
| Context | `instructions.md` at the context root beside `context.yaml` | Company, team, or project vocabulary, role refinements, local boundaries, and context-specific approval expectations |

The context file is recognized only for a valid Work Context root; an unrelated repository-level
`instructions.md` is not claimed as a personalization layer.

The user layer is merged first. The context layer follows it, so a reader treats the context layer
as higher precedence when the two conflict. Work Context labels each layer and adds a
`from <path>` provenance line in every generated bridge that receives personalization.

The files remain user-owned data. Work Context never creates, overwrites, deletes, or executes
them. `workctx agent install` reads their current bytes while preparing its plan and regenerates
only authenticated Work Context-owned bridges after approval. A bridge that existed before its
first install remains user-owned and is not modified; status reports that its layers were not
merged. Reinstall and upgrade read the layer files again, while uninstall leaves them in place.

## What to put in a layer

Good user-layer content is durable across work contexts:

```markdown
# Working style

- Speak as a concise engineering collaborator.
- Separate verified facts from assumptions.
- When a request is ambiguous, prepare a reversible proposal before changing local state.
```

Good context-layer content narrows that baseline for one fictional company or project:

```markdown
# Project Aurora role and boundaries

- In this context, act as an implementation reviewer for Project Aurora.
- Use the project glossary from the context before introducing a new term.
- Draft external updates, but do not send or publish them without explicit approval.
```

This is a company/project split using fictional content: put the stable company-wide
working relationship and tone in the user layer, then put the selected project's role, vocabulary,
and boundaries in that context's layer. A second project can supply a different context layer
without duplicating the user's baseline or changing another isolated context.

## What not to put in a layer

Do not store passwords, API keys, access tokens, private keys, or other secret values. Store only
approved secret references and resolve values through the
[secret-reference system](../reference/secrets.md). The validation diagnostic
[`CTX-POSSIBLE-SECRET`](../reference/validation-diagnostics.md) describes the same repository-wide
rule. Each layer is limited to 64 KiB and is secret-scanned independently; a possible secret
refuses the merge with only the layer name and line number in the diagnostic.

Do not use personalization as a security boundary. Text that asks an agent to bypass context
isolation, secret controls, mutation policy, or external-write approval remains untrusted text;
the agent may ignore it, and deterministic Work Context safety and approval gates still apply.
Likewise, do not put executable scripts or commands here expecting Work Context to run them. The
loader treats all content as inert Markdown and only copies validated bytes into generated
instruction bridges.

## Inspecting an install

Run `workctx agent install` without `--yes` to review the plan. Present layers appear as verification
entries before approval, including whether each will be merged. `workctx agent status --json`
reports each layer's path, byte size, and current merged state. A changed layer makes an
authenticated generated bridge stale; rerun installation to refresh it from the current layer
files.

## Merging your edits with updates

When a managed canonical skill or generated bridge has both local edits and a newer packaged
version, `workctx agent status` and the next install plan report the path together with three
exact-byte hashes: `recorded-at-adoption`, `packaged-now`, and `local`. This is a review marker,
not an automatic merge. Work Context preserves the local file, and approval of the install plan
does not authorize replacing or silently adopting it.

Ask an agent to draft the intended result from those three versions, then review the draft and
approve the file change yourself. Move durable custom instructions into the user or context
`instructions.md` layer; for a context-specific skill body, use the override mechanism below.
Restore the managed file to the current packaged or rendered bytes as part of that approved
change. The next `workctx agent install` verifies those exact bytes and records the file as tracked
again. If the approved file remains different from `packaged-now`, Work Context continues to
preserve and report it as operator-edited.

## Per-context skill overrides

A context can replace the `SKILL.md` body of one packaged Work Context skill without changing the
packaged kit. Overrides are context-local only; there is no user-level override layer. The fixed
location is:

```text
06_overrides/skills/<skill-name>/SKILL.md
```

The file is user-owned. Work Context never creates, overwrites, deletes, executes, or silently
merges it. Installation reads the current bytes, applies the same skill lint used for packaged
skills, and writes the override content to the selected client's installed skill output. Packaged
auxiliary resources and the registry classification remain in effect.

### Adopt an override

Start from the packaged skill version you intend to customize. After its YAML frontmatter and
before its authored body, add the exact adoption provenance block:

```markdown
---
name: fictional-review
description: Use when reviewing a fictional project change with traceable evidence.
---
<!-- workctx-skill-override:start -->
source: 06_overrides/skills/fictional-review/SKILL.md
packaged-at-adoption: sha256:<64 lowercase hexadecimal characters>
<!-- workctx-skill-override:end -->

# Fictional review override

Use the context-specific review sequence.
```

The `packaged-at-adoption` value must be the exact-byte SHA-256 content hash of the packaged
`SKILL.md` used as the starting point, including the `sha256:` prefix. After an ordinary packaged
install, the applicable `98_state/agent-adapters/<client>/skill-manifest.json` entry exposes that
value as `skills[].canonical.content_hash`. Creating the override remains a manual or explicitly
approved context transaction; `workctx agent install` never materializes it for you.

Run `workctx agent install` without `--yes` after creating the file. Override verification entries
appear before mutation entries and show:

- `packaged-at-adoption`: the packaged content hash recorded in the user-owned provenance block;
- `packaged-now`: the content hash shipped by the currently running Work Context kit;
- `override`: the exact content hash of the complete user-owned override file.

An override directory whose name is not in the packaged skill registry produces an
`unknown_skill` status warning and is ignored. It does not make installation invalid merely by
being unknown.

### Packaged upgrades and intentional rebases

When `packaged-at-adoption` differs from `packaged-now`, `workctx agent status` and the next install
plan report `override written against an older packaged skill` with all three hashes. This is a
review marker only: it does not block installation, choose changes, or perform a three-way merge.
The installed skill continues to use the override bytes exactly as authored.

To rebase intentionally, compare the old packaged version, the current packaged version, and the
override yourself. After accepting the desired edits, update `packaged-at-adoption` to the exact
current packaged hash. Do not change that marker merely to silence status; changing it asserts
that the override was reviewed against those exact packaged bytes.

### Remove an override

Delete only `06_overrides/skills/<skill-name>/SKILL.md`, then review and approve a new
`workctx agent install` plan. The next install restores the current packaged skill behavior. Empty
override directories may be removed separately; uninstall and install never claim them.

Each override has the same 64 KiB cap and secret scan as one personalization layer. A possible
secret refuses loading with only the portable override file and line number in the diagnostic.
Keep secret values out of the file, and remember that passing validation does not make authored
Markdown executable or grant it authority over Work Context safety and approval controls.
