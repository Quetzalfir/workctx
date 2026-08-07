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

Origin: real operator usage surfaced the links-directory case — the data was
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

## C-209 — Code repositories guide + curation tiering (operator-requested, 2026-08-03)

Two halves, both small:

1. Public guide `docs/guides/code-repositories.md`: the two working modes
   (referenceable fichas -> on-demand deep dive; active deep review whose
   conclusions persist), `repo://<id>@<commit>/<path>#L..` locator usage, the
   one-time `gh auth login` setup, and the investigate-system skill flow.
2. Explicit THREE-TIER curation rule, added to the curate-knowledge and
   process-evidence skills and to the guide, answering "does every URL get
   saved the same way?" — NO:
   - Tier 1 entity: core repos/systems of the project or team (own ficha,
     typed relations, appears in the resource directory, accumulates
     observations);
   - Tier 2 reference: useful sources that support existing knowledge — a
     `references` entry or a source ref inside an evidence note; searchable,
     no entity;
   - Tier 3 nothing: one-off helpful links; may be mentioned in a note body,
     never canonicalized.
   Default when unsure: tier 2, promote to tier 1 only on second real use
   (mirrors "prefer improving an existing entity over a redundant note").

## C-211 — Secret references, pulled forward from Phase 3 (operator decision, 2026-08-03)

Operator wants the secrets story now. Scope for a first slice, honoring the
invariant (values never in workspace, source control, logs, prompts, reports):

- `secret-ref` convention: canonical files may name a secret
  (`secret_ref: github-token`), never hold a value;
- resolver seam backed by the OS credential store (Windows Credential
  Manager / macOS Keychain / Secret Service via keyring), with env-var
  fallback for CI;
- `workctx secret set|check|list` CLI: `set` prompts interactively and writes
  ONLY to the OS store; `check` verifies resolvability without printing
  values; `list` shows names only;
- validation keeps refusing secret-shaped VALUES everywhere; a mini-ADR
  records the resolver design and its boundaries.

Needs its own work package next wave; not foldable into WP-600/610.

## C-211 addendum — dev ergonomics (operator, 2026-08-03)

Operator wants secrets effortless, including agent-driven registration in dev.
Adopted scope additions:

- `workctx secret import <.env-file>`: bulk-imports NAME=value pairs into the
  OS credential store, then offers to shred the file — one command to migrate
  an existing dev setup;
- agent-orchestrated registration: the agent runs `workctx secret set NAME`
  and the VALUE is typed/pasted by the human into the masked interactive
  prompt, so it never transits chat, transcripts, or agent logs;
- env-var fallback (`WORKCTX_SECRET_<NAME>`) for throwaway dev shells and CI.

Explicitly REJECTED: plaintext secrets inside the workspace/repo, even
local-only — it violates the core invariant, local repos get pushed, synced,
and read whole by agents, and the OS-store path above is equally easy. The
guide must state this with the import command as the answer.

## C-212 — Usage-driven relevance and decay (operator idea, 2026-08-03)

Extends C-202. The system observes its own read paths (search hits, ref
resolutions, context-pack inclusions, MCP reads) and manages information
importance over time:

- promotion: a tier-2 reference consulted/passed repeatedly gets a suggested
  promotion to tier-1 entity ("referenced 7x this month — promote?");
- decay: a task or claim losing references and activity gets a suggested
  degradation (close, archive, supersede) with the inactivity evidence.

Design principles (non-negotiable): usage telemetry is machine-local
advisory state under 98_state (never canonical, never synced, rebuild-safe
to delete); the system SUGGESTS via views/brief and every promotion or
degradation lands as an ordinary approvable transaction — nothing mutates
silently. Optional per-class auto-approve policy can come later, opt-in.
Needs real design (what counts as a use, thresholds, anti-noise) — its own
package, likely after C-202's suggestion pipeline exists.

## C-213 — Public operation-session API (technical debt, 2026-08-03)

WP-660's batch registration necessarily consumes private transaction-engine
and projection internals (_HeartbeatLease, _OperationCache,
_run_with_heartbeat, diagnostic helpers, _begin/_end_locked_operation)
because a batch spans one lock/heartbeat/projection scope and no public
session primitive exists. Accepted at integration with this debt note.
Next package that touches the engine or ingestion performance MUST promote a
public operation-session API (engine-owned object bundling lock, heartbeat
lease, operation caches, and projection operation scope) and move ingestion
onto it. Behavior is fully test-covered, so drift breaks loudly — the risk is
maintenance friction, not silent corruption.

## C-214 — Generic declarative connector runtime (operator direction, 2026-08-03)

Operator challenged the per-service connector catalog: private/internal
services must work without workctx shipping code for them. Adopted Phase 3
architecture, three levels:

- Level 0 (exists): the inbox IS the universal ingestion contract — any
  source delivers evidence via 00_inbox/raw + inbox add / artifact_register,
  with hashing, provenance, and quarantine. Nothing is blocked by a missing
  connector.
- Level 1 (Phase 3 spine): ONE generic snapshot engine executing declarative
  per-source YAML manifests (name, base_url, secret_ref, snapshot endpoints/
  queries, schedule, pagination hints). Adding a connector = writing a
  manifest, including for private services. Snapshots carry full provenance
  (system, query, timestamp, hash) and enter the normal evidence pipeline.
  Responses are untrusted data; quarantine rules apply unchanged.
- Level 2 (on demand only): thin named adapters over the runtime for sources
  needing real logic (OAuth flows, odd pagination, binary exports, Teams).
  Built when demand exists, never by catalog. Vendor MCP servers remain the
  agent-side interactive path.

External WRITES stay out of the runtime: drafts in 05_outbox, per-operation
explicit approval, unchanged.

Phase 3 cut consequence: first package is the manifest spec + engine +
scheduler; the operator's 2-3 daily systems get manifests (and thin adapters
only if the generic engine falls short).

## C-215 — Adapter recovery and freshness package (operator-driven, 2026-08-06) — DELIVERED (WP-760, merged 1c31073, 2026-08-06)

Three pieces from one day of real operator use, cut together:

1. `workctx agent forget <context>`: officially drop one context's entry from
   the machine-local trusted install record. Today's trust-divergence
   recovery required hand-editing JSON because the three-factor check blocks
   even uninstall (circular recovery, verified live).
2. Pristine-skill refresh on `agent install`: context-canonical skills under
   `.agents/skills/` pin the versions from the day the context was born;
   packaged-skill improvements never reach existing contexts. Same cure as
   the template bridge: byte-hash against every historical packaged version,
   refresh pristine files, never touch operator-edited ones.
3. Register-on-use: any context-resolved command best-effort registers an
   unregistered context (covers pre-registry, cloned, and copied contexts).
   Hazard proven live: fictional test contexts polluted the operator's real
   registry — needs suite-wide registry isolation plus never-fail semantics
   before the hook lands.

### C-215 addendum — merge-assist for edited-and-outdated managed files (2026-08-06)

Fourth piece: when a managed file (contract, bridge, canonical skill) is BOTH
operator-edited and behind the packaged version, deterministic code surfaces
the three-way state (packaged-at-adoption, packaged-now, local) — never
auto-merges — and a skill guides an agent to draft the merged version as an
approvable transaction. Deterministic detection, AI-proposed merge, human
approval. The skill-override three-way marker (WP-690) is the working
precedent. Operator scenario: an agent recorded standing AWS-access
instructions in AGENTS.md, which preserves them but freezes that file out of
future contract updates; the personalization layer is today's correct home,
and merge-assist closes the general case.

## C-216 — Fleet refresh: one command to update every registered context — DELIVERED (WP-770, 2026-08-07)

Operator request (2026-08-06): "si workctx ya sabe dónde están, ¿no podría
haber un comando que los auto-actualice a todos?" Now that the context
registry (WP-750) plus register-on-use (WP-760) keep an accurate machine
inventory, a single command should refresh agent adapters across it.

Proposed shape: `workctx agent refresh --all [--yes] [--agent <name|all>]`.
Per registered context: skip missing roots with a warning, plan per
available client, apply only with the explicit batch `--yes` (D-045: nothing
auto-approves), never abort the batch on one context's failure, and end with
a per-context summary table (refreshed / preserved-edits / merge-pending /
skipped / failed) plus a non-zero exit if anything failed. Without `--yes`
it is a fleet-wide dry-run preview. Reuses the existing install planner
verbatim; no new mutation machinery.

## C-217 — Version visibility

`workctx --version` does not exist (operator hit this while diagnosing a
stale-install incident, 2026-08-07); version is only discoverable via
package metadata. Add an eager `--version` flag and include the version in
`workctx doctor` output so version skew between installs, MCP servers, and
contexts is diagnosable in one command.

## C-218 — Agent bridge hardening: orient-first, ask-once — DELIVERED (WP-780, 2026-08-07)

Operator report (2026-08-07): agents inside real contexts keep asking for
facts the context already holds (GitHub access method, people and roles,
permission flows). Two systemic gaps: agents do not orient themselves in
the stored knowledge before asking, and repeated operator answers are not
persisted, so the same question returns.

Harden the packaged bridges, context template, and orientation-relevant
skills with two mandatory rules:
1. Orient before asking — at task start consult context.yaml policies and
   the generated views (people-directory, resource-directory, glossary,
   current-focus); before requesting any fact or access from the operator,
   exhaust `workctx search`, `workctx ref`, `90_integrations/`,
   `workctx secret list`, and `workctx connector list` (extends the
   existing access-discovery rule beyond access to all context knowledge).
2. Ask once, record forever — any fact the operator supplies in chat that
   the context lacked MUST be persisted the same session through the
   normal proposal flow (entity, integration note, or instructions.md
   suggestion), so no agent asks for it again.

Existing contexts receive this via WP-760 pristine freshness on the next
`agent refresh --all`.
