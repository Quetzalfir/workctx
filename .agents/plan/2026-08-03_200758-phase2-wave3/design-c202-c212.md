# Design: C-202 adoption machinery + C-212 usage telemetry

Status: DRAFT for operator review (2026-08-03). The detection half of C-202
ships in WP-650 as the suggestions view. This document designs the remaining
halves and names the product decisions that belong to the operator.

## C-202 adoption: suggestions that become durable changes

### Mechanism

1. A suggestion (from the view or from an agent's own finding) is promoted to
   a SUGGESTION RECORD: a small canonical document under
   `03_work/suggestions/SUG-<date>-<slug>.md` with type (data-fix |
   skill-override | engine-proposal), rationale, the signal/evidence refs,
   and a concrete proposed change. Created via ordinary approved transaction
   (agents can draft them; nothing lands without approval).
2. Adoption per type:
   - data-fix: the suggestion links a ready transaction proposal; approving
     it applies the fix and marks the record adopted (supersession keeps
     history).
   - skill-override: adoption materializes an override file under
     `06_overrides/skills/<skill-name>/SKILL.md` inside the CONTEXT. The
     skill loader (adapters) merges overrides over packaged skills at
     install/render time, with provenance ("override from <path>") and a
     three-way marker so a kit upgrade shows packaged-old vs packaged-new vs
     override. Overrides are user-owned files like personalization layers:
     size-capped, secret-scanned, never executed.
   - engine-proposal: adoption = the agent drafts a GitHub issue/PR text
     into 05_outbox (never sends); the operator files it upstream.
3. The suggestions view gains a "Records" section listing open suggestion
   records with ages, so nothing silently rots.

### Safety properties

Same rails as everything else: records and overrides are canonical Markdown,
mutations go through approved transactions, the ledger records adoption, and
evidence quarantine rules apply to suggestion bodies (a suggestion sourced
from hostile evidence cannot smuggle instructions — records are data that a
HUMAN approves before any skill override exists).

## C-212 usage telemetry: promotion/decay signals

### Mechanism

1. Instrumentation seam in read APIs (search, resolve/trace, context-pack
   build, MCP reads): each call appends one compact line
   (timestamp, api, uri/query-hash) to `98_state/usage/usage.jsonl` —
   machine-local, append-only, size-rotated, NEVER canonical, NEVER synced,
   deletable at any time with zero data loss. No file contents, no bodies,
   no secret-adjacent text — URIs and hashed queries only.
2. A deterministic aggregator (`workctx usage summarize`, also run inside
   view rebuilds when the file exists) folds usage into per-URI counters
   over rolling windows (7/30/90 days).
3. Promotion/decay rules produce SUGGESTIONS (never actions), feeding the
   C-202 pipeline: tier-2 source referenced >= N times in 30 days -> suggest
   tier-1 promotion; task/claim untouched by usage AND ledger for M days ->
   suggest close/supersede.

## Operator decisions required (numbered; recommendation first)

1. Auto-approve policy for adoptions.
   Option A (recommended): nothing auto-approves in v1 — every adoption is a
   human-approved transaction; revisit after a month of real usage.
   Option B: allow auto-approve for data-fix suggestions whose proposal only
   touches `updated_at`/status-refresh class changes.
2. Telemetry default.
   Option A (recommended): opt-in per context (`context.yaml` flag,
   default off) — usage recording starts only when the operator enables it.
   Option B: on by default with `workctx usage off` to disable.
3. Thresholds N (promotion) and M (decay).
   Option A (recommended): N=5 in 30 days, M=60 days, tunable per context in
   context.yaml; start conservative, let the suggestions view prove signal
   quality before tightening.
4. Scope of skill overrides.
   Option A (recommended): per-context only in v1 (an override lives in one
   context); user-level overrides later if real need appears.
   Option B: user-level overrides too, merged personalization-style.

## Proposed packaging (next cut, pending decisions)

- WP-680: suggestion records + data-fix adoption + view section (no skill
  overrides yet) — smallest safe slice.
- WP-690: skill-override loader + three-way upgrade marker.
- WP-700: telemetry seam + aggregator + promotion/decay suggestions.
