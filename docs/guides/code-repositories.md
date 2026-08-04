# Code repositories

A code repository is a system your context knows about, not a context of its own.
Repositories belong to the project context that owns them, and the conclusions you reach
while reading code become durable knowledge in that context — pinned to the exact commit
and lines they came from, so they stay checkable after the branch has moved on.

There are two ways to work with a repository, and they compose.

## Mode 1: register once, deep-dive on demand

Give each core repository its own entity — a `system`, `service`, or `module` record
under `02_knowledge/` — one time. The entity carries the repository's identity and typed
relations, and it accumulates the observations that later investigations leave behind.
Agents then answer routine questions from accumulated knowledge and open the code only
when a question actually requires it.

A fictional billing exporter registered as a system entity:

```yaml
---
schema_version: 1
id: SYS-atlas-billing
entity_type: system
title: Atlas billing exporter
uri: workctx://atlas-project/system/SYS-atlas-billing
aliases: [atlas-billing]
status: active
confidence: high
tags: [repository]
references:
  - relation: related_to
    target: https://github.example/acme/atlas-billing
access_urls:
  - url: https://github.example/acme/atlas-billing
    label: GitHub
    access: sso
created_at: "2026-08-03T00:00:00Z"
updated_at: "2026-08-03T00:00:00Z"
---
```

Register it by asking your agent (one small reviewed transaction) or by authoring the
file yourself and validating and rebuilding derived state from inside the context:

```powershell
workctx validate
workctx index rebuild
workctx view rebuild
```

Registered system, service, and integration entities appear in the generated resource
directory (`04_views/resource-directory.md`, documented in the
[views reference](../reference/views.md)), so "what do we run and where does it live"
stays answerable without scrolling chat history.

## Mode 2: active deep review

When you need a real answer from the code — why is this slow, who calls this, what
changed — run it as an investigation rather than a throwaway chat. The difference is
what survives: the investigation's conclusions persist as observations with exact
repository locators, attached to the investigation that produced them, and are promoted
to the owning system entity once they are stable and reusable.

## Repository locators

Durable code references use the `repo://` form defined in the
[reference system](../reference/reference-system.md):

```text
repo://<repo-id>@<commit>/<path>#L<start>-L<end>
```

The commit is required. Branch names are not durable locators — a branch moves, and a
finding cited against it silently stops being checkable. A commit plus a line range
stays verifiable forever.

### Worked example: a cost investigation (fictional)

The Atlas team's cloud bill doubled in July. The operator asks the agent to investigate.
The agent starts from the registered `SYS-atlas-billing` entity, reads the exporter
code, and finds an upload retry loop with no backoff at commit `a1b2c3d`:

```text
repo://atlas-billing@a1b2c3d/billing/export.py#L41-L58
```

After review and an approved transaction, what persists is:

- an observation locating the retry loop at exactly those lines;
- a claim — "the exporter retries failed uploads without backoff" — recorded as fact
  with that observation as its source;
- a task to add backoff, related to the claim;
- all of it attached to the `SYS-atlas-billing` entity, not to a chat transcript.

Months later, anyone can rediscover and verify the finding:

```powershell
workctx search "retry backoff" --type claim
workctx ref trace workctx://atlas-project/claim/CLM-2026-00042
```

`ref trace` walks from the claim back through its observation to the `repo://` locator,
so the finding remains checkable against the cited commit even after the default branch
has moved on.

## Machine setup for private repositories

Reading a private GitHub repository requires authenticating the machine once:

```powershell
gh auth login
```

`gh` stores the resulting token in the operating system's credential store. The token
must never appear anywhere else: not in chat with an agent, not in canonical files, not
in evidence, not in logs or reports. workctx enforces the workspace side of this
guardrail — validation rejects secret values and the inbox quarantines suspected
secrets. For secrets workctx itself manages, use first-class
[secret references](../reference/secrets.md): a canonical file names a secret with
`secret_ref` and a local resolver supplies it at use time.

## Investigations from the user's side

The portable `investigate-system` skill drives the deep-review mode (see
`.agents/skills/investigate-system/SKILL.md`). As the operator you experience it as
five steps:

1. You ask the question and set its boundaries: what to include, what to exclude, and
   how confident the answer must be.
2. The agent checks local knowledge first — accumulated observations, prior decisions,
   typed relations — before it opens any code.
3. External reads stay read-only and inside the access you have configured; the
   investigation never mutates a remote system.
4. Every material finding comes back classified as fact, inference, or assumption, with
   an exact locator you can follow.
5. Nothing becomes canonical on its own: persisting the findings is a separate reviewed
   transaction that you approve.

## Not every URL becomes an entity

Repositories, documentation sites, dashboards, and one-off links do not all deserve the
same treatment. workctx uses three tiers:

| Tier | What it is for | What gets written |
| --- | --- | --- |
| 1 — entity | Core repositories and systems of the project or team | Its own entity record with typed relations; appears in the resource directory; accumulates observations |
| 2 — reference | Supporting sources that back existing knowledge | A `references` entry on an existing entity, or a source reference inside an evidence note; searchable, but no entity |
| 3 — nothing | One-off helpful links | At most a mention in a note body; never canonicalized |

Default to tier 2 when unsure, and promote a source to tier 1 on its second real use.
This mirrors the curation rule of preferring to improve an existing entity over adding a
redundant note: an entity is a commitment to maintain, so a URL earns one by being used,
not by being seen.

## See also

- [Reference system](../reference/reference-system.md) — locator grammar and ID forms.
- [Generated views](../reference/views.md) — the resource directory and its `access_urls` field.
- [Evidence processing](evidence-processing.md) — how findings become canonical.
- [Multiple contexts](multiple-contexts.md) — why a repository is not a context.
- [Security and privacy](../security-and-privacy.md) — trust model and approval gates.
