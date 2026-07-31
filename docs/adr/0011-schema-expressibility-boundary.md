# ADR 0011: Expressibility boundary for schema/model alignment

- Status: accepted
- Date: 2026-07-30

## Context

ADR 0008 requires negative fixtures rejected by BOTH the JSON Schema and the Pydantic
model. Standard JSON Schema Draft 2020-12 cannot express several contract invariants:
cross-field relations (a pack's focal URI embedding its own context id), arithmetic
(budget totals, omitted-count equality), and derived values (weighted rank sums).
WP-230 correctly blocked on this rather than weakening enforcement silently. The same
class already shipped, reviewed and accepted, in WP-100 (locator range ordering enforced
in Python only) and WP-110 (entity URI type/id equality as a tested code-only invariant).

## Decision

- Contract constraints are classified into two tiers:
  - **Structural constraints** — expressible in standard Draft 2020-12. These follow
    ADR 0008 unchanged: negative fixtures must be rejected by BOTH schema and model.
  - **Producer invariants** — relations Draft 2020-12 cannot express (cross-field
    equality, arithmetic, derived values, referential resolution). These are enforced by
    the Pydantic models and the producing code, negatively tested at the model level, and
    MUST be listed in the schema's top-level `description` and in the contract's
    reference documentation so schema consumers know validation is necessary but not
    sufficient.
- Non-standard schema dialects, vendor extensions, and `$data`-style references are
  rejected: the public schemas stay portable standard Draft 2020-12.
- A constraint may not be classified as a producer invariant when a reasonable standard
  representation exists (e.g. use `const`/`enum` restructuring before giving up); the
  work order's leader review checks this.
- ADR 0008's "rejected by BOTH" sentence is refined — not reversed — to apply to the
  structural tier.

## Consequences

- WP-230 unblocks with a bounded change: split negative fixtures into the two tiers and
  document the producer-invariant list; Pydantic enforcement stays exactly as built.
- Schema consumers get an explicit, discoverable statement of what schema validation
  alone does not guarantee.
- Existing accepted deliveries (WP-100, WP-110) already conform; no rework.
