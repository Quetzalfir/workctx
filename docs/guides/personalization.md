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

This is a JalaSoft-style company/project split using fictional content: put the stable company-wide
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
