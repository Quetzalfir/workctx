# Phase 2 candidates (operator-requested, 2026-08-03)

Two feature themes requested by the operator after the Phase 1 close. Both are
recorded in ROADMAP.md under Phase 2; this file holds the design intent so the
future wave lead does not have to reconstruct it from conversation.

## C-201 — Portable personalization layers

Problem: today per-user and per-project instructions ("answer in this tone",
"in this situation do not make changes", "our role is X") live in each agent's
own mechanism (e.g. the Claude global/project bridge files), so they are
written per agent and drift apart.

Intent: two canonical instruction layers owned by workctx —

1. user-level, one file per machine user (location: the same per-user
   directory that already holds the context registry);
2. context-level, one canonical file inside the workspace.

The agent installer merges both layers into every generated bridge/adapter at
install and upgrade time, for all supported clients. Precedence: context over
user. Layers are plain Markdown, validated only for size and secret-scan.
Upgrades must never discard a layer (three-factor install records already
distinguish generated from user-owned content).

## C-202 — Assisted improvement loop

Problem: the assistant should notice quality drift — missing references, stale
structure, weak output contracts, better processing steps — and improve the
system, but improvements must not bypass review or mutate engine behavior.

Intent: three explicitly separated improvement targets —

1. workspace data: already served by validation diagnostics, stale-knowledge
   views, and ref tracing; suggestions become ordinary approved transactions;
2. local skills/templates: a suggestion workflow (finding -> reviewable
   suggestion -> adopted local override) where adopted overrides are versioned
   in the context and survive kit upgrades; needs an override mechanism in the
   skill loader plus provenance so an upgrade can show a three-way diff;
3. the workctx engine itself: out of runtime scope by design — the agent may
   draft an issue/proposal for the open-source repository, never hot-modify
   deterministic behavior.

Constraint carried from Phase 1: suggestions are data, not instructions; the
loop must not create a channel where evidence content can smuggle behavior
changes into skills.

## C-203..C-207 — First-class homes for everyday work data (operator-requested, 2026-08-03)

Origin: real JalaSoft usage surfaced the links-directory case — the data was
representable (entities are extensible, `references` is typed) but nothing
generates a usable projection, so each agent improvises a pattern. A sweep for
sibling cases found the same shape everywhere. Design principle for ALL of
these: no new entity types (D-018 vocabulary stays frozen) and no new storage —
each is a GENERATED VIEW over canonical data the workspace can already hold.

- C-203 resource directory: `04_views/resource-directory.md` generated from
  system/service/integration entities' `references`/`access_urls`, grouped by
  access requirement (public, SSO, VPN); includes the environments matrix
  (dev/staging/prod URLs). Never stores credentials — pairs with Phase 3
  secret references.
- C-204 people and teams directory: generated from person/team entities —
  role, team, channels, timezone, and what each person currently owns or
  blocks (join with tasks).
- C-205 glossary: generated from entity aliases plus first-line definitions —
  the acronym decoder (HCM, MOI, ...) every company context needs.
- C-206 agenda and deadlines: date-ordered view over task `due_at` and
  waiting-on ages; the brief shows today, this shows the horizon.
- C-207 status report generator: period summary (what committed, what moved,
  what blocked) derived from ledger activity and task transitions, emitted as
  a draft for the operator's manager-facing reporting. Highest-leverage of the
  five for daily use.

Recorded for the Phase 2 cut; C-203 and C-207 look like the best first pair.

## C-210 — Company-over-projects federation (deferred to Phase 4)

Operator asked for it during Phase 1 close: consolidated views from a company
context across its sibling project contexts. Registered so it stops being an
orphaned conversation item, but it crosses the context security boundary and
belongs with Phase 4 shared-context design, not Phase 2.
