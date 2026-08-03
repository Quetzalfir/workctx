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

## C-208 — Mutation-path performance pass (measured 2026-08-03)

Operator reported slow first-evidence processing. Lead profiled on Windows 11
(NTFS + Defender). Facts, per single small-file registration (~4s fixed cost,
nearly size-independent: 10 lines vs 5 MB differ by 0.6s):

- 1,232 file opens (0.89s) — repeated re-opening of locks, snapshots, and
  canonical files within one operation;
- 30 SQLite `executescript` schema initializations (0.64s) — a fresh
  connection + schema per internal query instead of one per operation;
- 8,618 `Path.resolve` final-path calls (0.56s) and 8,679 stats (0.33s) —
  boundary checks re-resolve the same roots thousands of times;
- 133 fsyncs (0.38s) — the only cost that is durability by design;
- YAML re-parsing of canonical files several times per apply (~0.5s);
- lock heartbeat ~40ms per write, 2 sync writes per engine step (16 steps).

Bulk apply measured at 9.5s for 150 entities (~64ms/entity); FTS search 8ms;
view rebuild 0.19s; ledger verify 8ms — read paths are healthy, the ceremony
around mutations is not. Raw disk is innocent (write+replace 0.9ms).

Directions (correctness-preserving, no ADR changes expected): one SQLite
connection with schema-once per operation; resolve the context root once per
locked operation and join relative paths without re-resolving; cache canonical
reads within a single apply; heartbeat piggybacking (write only when lease age
exceeds half the interval); amortize multi-file `inbox add` under one lock and
one projection refresh. Target: register < 0.5s, small apply < 1.5s.

Single-writer-per-context stays by design — parallelism belongs to read paths
(already safe) and to the agent layer, not to canonical writes.
