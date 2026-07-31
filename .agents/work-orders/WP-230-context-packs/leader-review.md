# Leader review: `WP-230-context-packs`

## Decision

`revision_requested`

The blocker was the correct call — the worker refused to weaken ADR 0008 silently. The
lead decision it asked for is now recorded as ADR 0011 (expressibility boundary): the
inexpressible relations are **producer invariants**, enforced by Pydantic and negatively
tested at model level; the JSON Schema stays standard Draft 2020-12 and must declare the
producer-invariant list in its top-level description.

## Contract compliance (interim)

- Merge base `0343911` matches the pinned contract base (the branch's own frozen copy
  predates the pin, as in prior orders — accepted). Delivery `3a1b65d`, report `a4ca100`.
- Changed-path audit: clean; worker-reported gate: full suite green on the branch
  (462 tests) including 70 retrieval tests. Final independent gate runs at acceptance.

## Failed criterion and evidence

- Criterion: "Packs validate against context-pack.schema.json (positive and negative
  fixtures per ADR 0008)". Evidence: focal-uri/context-id equality, budget arithmetic,
  omitted-count equality, and rank-total relations are Pydantic-enforced but not
  expressible in standard Draft 2020-12, so both-reject fixtures for them cannot exist.

## Required correction (bounded; scope otherwise unchanged)

1. Read docs/adr/0011-schema-expressibility-boundary.md.
2. Split negative fixtures into the two ADR 0011 tiers: structural fixtures keep
   both-reject tests; producer-invariant fixtures assert model rejection only.
3. Add the producer-invariant list to context-pack.schema.json's top-level description
   and to docs/reference/context-packs.md (validation is necessary, not sufficient).
4. Classify the rank-total relation per ADR 0011: producer invariant (the serialized
   value is authoritative; consumers do not recompute it) — document that choice.
5. Re-check whether any currently-inexpressible constraint has a reasonable standard
   representation before classifying it (ADR 0011 requires this check; document the
   outcome per constraint in the report).
6. Rerun all validation commands; update report.md/report.json to status completed.

Same branch and worktree continue. Commands that must pass: the four gate commands.

## Integration notes

- On acceptance: integrate, rerun combined gate, wire ref/context-pack CLI commands
  (lead), close Wave 2.
